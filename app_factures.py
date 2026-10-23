# -*- coding: utf-8 -*-
"""FACTURES IA v20 - IA D'OR : trios HT/TVA/TTC, OCR fusion multi-lectures
AVEC REDRESSEMENT DES SCANS PENSES, filtre anti-SIRET, suivi des paiements
PRO, pack comptable par mois, export parametrable, ASSISTANT IA local et
SAUVEGARDE PERMANENTE via Supabase (si configure dans les Secrets) avec
MIGRATION SECURISEE depuis SQLite (l'ancien fichier factures.db est GARDE).
Regle d'or : jamais inventer, jamais 'payee' sans confirmation humaine."""

import re
import io
import os
import json
import zipfile
import unicodedata
import datetime
import sqlite3

# --- Requetes vers Supabase (sauvegarde permanente) : facultatif ---
try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

import streamlit as st
import pymupdf  # PyMuPDF
import pandas as pd

# --- OCR (Tesseract) : facultatif, installe si present ---
try:
    import pytesseract
    from PIL import Image, ImageOps
    CHEMIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(CHEMIN_TESSERACT):
        pytesseract.pytesseract.tesseract_cmd = CHEMIN_TESSERACT
    HAS_OCR = True
except Exception:
    HAS_OCR = False

# ============================================================
# 1. OUTILS DE BASE
# ============================================================

def _score_ocr(txt):
    """Note la qualité d'un texte OCR : on préfère celui qui contient
    des dates, des montants à décimales et des mots-clés comptables.
    Comme ça, quand on essaie plusieurs lectures, on garde la MEILLEURE."""
    if not txt:
        return -1
    s = 0
    s += 3 * len(re.findall(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", txt))
    s += 2 * len(re.findall(r"\d+[.,]\d{2}\b", txt))
    tn = norme(txt)
    for mot in ("total", "tva", "ht", "ttc", "facture", "invoice",
                "net a payer", "echeance", "hors taxe"):
        if mot in tn:
            s += 2
    return s


def _redresser_image(image):
    """Détecte l'inclinaison/rotation d'un scan penché et le remet droit.
    (Tesseract OSD : 'Rotate: 90/180/270'). Silencieux si détection impossible."""
    try:
        osd = pytesseract.image_to_osd(image)
        m = re.search(r"Rotate:\s*(\d+)", osd)
        if m and m.group(1) in ("90", "180", "270"):
            image = image.rotate(-int(m.group(1)), expand=True)
    except Exception:
        pass
    return image


def _preparer_image(png_bytes, binariser):
    """Nettoie l'image avant l'OCR : redressement si le scan est penché,
    niveaux de gris + contraste automatique, et si demandé binarisation
    noir/blanc (efficace sur les scans ternes)."""
    image = Image.open(io.BytesIO(png_bytes))
    try:
        image = _redresser_image(image)
        image = image.convert("L")
        image = ImageOps.autocontrast(image)
        if binariser:
            image = image.point(lambda x: 255 if x > 160 else 0)
    except Exception:
        pass
    return image


def _ocr_image(image, psm=None):
    """Lance Tesseract sur une image préparée, français d'abord."""
    cfg = "--psm " + str(psm) if psm else None
    for lang in ("fra", "fra+eng"):
        try:
            return pytesseract.image_to_string(image, lang=lang, config=cfg)
        except Exception:
            try:
                return pytesseract.image_to_string(image, config=cfg)
            except Exception:
                pass
    return ""


def _lire_page_ocr(page):
    """Multi-essais sur une page : plusieurs résolutions et filtres.
    Retourne la liste des lectures différentes, de la MEILLEURE à la
    moins bonne (l'IA les utilisera toutes : le HT peut être bien lu
    dans une lecture et le TTC dans une autre)."""
    essais = []
    for dpi, binariser, psm in ((300, False, None), (300, True, 6),
                                (400, True, 4), (500, True, 6)):
        pix = page.get_pixmap(dpi=dpi)
        image = _preparer_image(pix.tobytes("png"), binariser)
        txt = _ocr_image(image, psm)
        essais.append(txt)
        if _score_ocr(txt) >= 14:
            break   # très bonne lecture, inutile d'insister
    # tri par score, on élimine les lectures quasi identiques
    classement = sorted(((_score_ocr(t), t) for t in essais), key=lambda x: -x[0])
    retenues = []
    for s, t in classement:
        if s < 3:
            continue   # lecture trop pauvre, on l'ignore
        if any(t[:60] == t2[:60] for t2 in retenues):
            continue   # doublon quasi identifique
        retenues.append(t)
        if len(retenues) >= 3:
            break
    if not retenues:
        retenues = [max(essais, key=_score_ocr)]
    return retenues


@st.cache_data(show_spinner=False)
def lire_texte_pdf(fichier):
    """Retourne (texte, methode) : 'texte du PDF' ou 'OCR (scan)'.
    Pour les scans : la MEILLEURE lecture est mise en premier (pour les
    libellés), puis les autres lectures différentes sont ajoutées en dessous
    (pour que le moteur IA puisse croiser les montants entre les lectures)."""
    doc = None
    try:
        doc = pymupdf.open(stream=fichier.getvalue(), filetype="pdf")
        texte = "\n".join(page.get_text() for page in doc).strip()
        if len(texte) >= 40:
            return texte, "texte du PDF"
        if not HAS_OCR:
            return texte, "texte du PDF"
        principales, secondaires = [], []
        for page in doc:
            lectures = _lire_page_ocr(page)
            principales.append(lectures[0])
            secondaires.extend(lectures[1:])
        texte_final = nettoyer_ocr("\n".join(principales).strip())
        if secondaires:
            texte_final += "\n" + nettoyer_ocr("\n".join(secondaires).strip())
        return texte_final, "OCR (scan)"
    except Exception:
        return None, ""
    finally:
        if doc is not None:
            doc.close()


def nettoyer_ocr(texte):
    """Corrige les erreurs classiques de l'OCR dans les chiffres :
    O lu a la place de 0, I/l lus a la place de 1 (uniquement autour de chiffres,
    pour ne jamais toucher les mots)."""
    texte = re.sub(r"(?<=\d)[Oo](?=\d)", "0", texte)
    texte = re.sub(r"(?<=\d)[Oo](?=[ \u00a0\u202f]*[.,]\d)", "0", texte)
    texte = re.sub(r"(?<=\d)[Il](?=\d)", "1", texte)
    texte = re.sub(r"(?<=\d)[Il](?=[ \u00a0\u202f]*[.,]\d)", "1", texte)
    texte = re.sub(r"(?<=\d)[Oo](?=[/\-.]\d)", "0", texte)   # dates : 1O/03/2026
    texte = re.sub(r"(?<=[/\-.])[Oo](?=\d)", "0", texte)
    return texte


@st.cache_data(show_spinner=False)
def lignes_par_position(fichier):
    """Reconstruit les lignes VISUELLES grace aux coordonnees des mots."""
    try:
        doc = pymupdf.open(stream=fichier.getvalue(), filetype="pdf")
        lignes = []
        for page in doc:
            mots = page.get_text("words")
            par_y = {}
            for m in mots:
                cle = round(m[1] / 4)
                par_y.setdefault(cle, []).append(m)
            for cle in sorted(par_y):
                mots_ligne = sorted(par_y[cle], key=lambda m: m[0])
                lignes.append(" ".join(m[4] for m in mots_ligne))
        doc.close()
        return lignes
    except Exception:
        return None


def norme(t):
    """Minuscules sans accents : 'Echeance' -> 'echeance'."""
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _pdf_depuis_lignes(lignes):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60
    for texte in lignes:
        page.insert_text((55, y), texte, fontsize=12)
        y += 22
    data = doc.tobytes()
    doc.close()
    return data

# ============================================================
# 2. 3 FACTURES DE TEST
# ============================================================

@st.cache_data(show_spinner=False)
def facture_test_1():
    return _pdf_depuis_lignes([
        "ENTREPRISE DUPONT & FILS", "24 avenue de la Gare - 75011 Paris",
        "Facture N° FA-2026-118", "Date de facture : 12/10/2026",
        "Echéance de paiement : 12/11/2026", "",
        "Description                     Quantité    Prix unitaire",
        "Conseil en organisation              10         125,00 EUR",
        "Formation comptabilité                2         250,00 EUR", "",
        "Total HT : 1 750,00 EUR", "TVA 20 % : 350,00 EUR", "Total TTC : 2 100,00 EUR",
        "Total à payer : 2 100,00 EUR", "Devise : EUR",
    ])


@st.cache_data(show_spinner=False)
def facture_test_2():
    return _pdf_depuis_lignes([
        "OCEAN SUPPLIES LLC", "85 Harbor Street - San Diego, CA",
        "INVOICE #INV-9923", "Invoice date: 10/03/2026", "Due date: 09/04/2026", "",
        "Item                     Qty     Unit price",
        "Marine rope coil           4        62,50 USD",
        "Safety vest               10        19,00 USD", "",
        "Subtotal: 440,00 USD", "VAT 5% : 22,00 USD", "Total TTC: 462,00 USD",
        "Balance due: 462,00 USD", "Currency: USD",
    ])


@st.cache_data(show_spinner=False)
def facture_test_3():
    return _pdf_depuis_lignes([
        "ATELIER CREOLE SARL", "Cite des Arts - 97400 Saint-Denis, La Reunion",
        "no de facture : 2026-307", "emise le 03.10.2026", "a payer avant le 03.11.2026", "",
        "Prestation                Montant",
        "Menuiserie sur mesure     320,50", "Livraison                   0,00", "",
        "Total hors taxes : 320,50", "Taux TVA 8,5 % - Montant TVA : 27,24",
        "Net a payer (TTC) : 347,74", "Reglement en euros (EUR)",
    ])

# ============================================================
# 3. EXTRACTION DE BASE (libelles)
# ============================================================
# Regle d'or : jamais inventer. Introuvable = vide = "A verifier".

NOMBRE = r"\d{1,3}(?:[ \u00a0\u202f.]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"


def _nombre(s):
    s = s.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "", s.count(".") - 1)
    try:
        return float(s)
    except ValueError:
        return None


def _date_std(d):
    if not d:
        return None
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", d)
    if not m:
        return d
    j, mois, a = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(a) == 2:
        a = "20" + a
    a = int(a)
    # Anti-bêtise OCR : une date invalide n'est pas une date
    if not (1 <= mois <= 12 and 1 <= j <= 31 and 1990 <= a <= 2099):
        return None
    return "%02d/%02d/%04d" % (j, mois, a)


def extraire_dates_ligne(ligne):
    retour = []
    for m in re.finditer(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", ligne):
        d = _date_std(m.group(0))
        if d and d not in retour:
            retour.append(d)
    return retour


def _montant_sur_ligne(ligne):
    ligne_sans_pourcent = re.sub(r"\d{1,2}(?:[.,]\d{1,2})?\s*%", "", ligne)
    for v in reversed(re.findall(NOMBRE, ligne_sans_pourcent)):
        n = _nombre(v)
        if n is not None:
            return n
    return None


def extraire_montant(t, etiquettes):
    """Priorite aux libelles precis. Le 'total' generique ne touche jamais une ligne HT/TVA."""
    lignes = t.splitlines()
    for etq in etiquettes:
        for i, ligne in enumerate(lignes):
            ln = norme(ligne)
            if not re.search(etq, ln):
                continue
            if etq == r"\btotal\b" and re.search(r"ht\b|hors\s*taxe|subtotal|sous[- ]total|\btva\b|\bvat\b", ln):
                continue
            v = _montant_sur_ligne(ligne)
            if v is not None:
                return v
            if i + 1 < len(lignes):
                v = _montant_sur_ligne(lignes[i + 1])
                if v is not None:
                    return v
    return None


def _numero_valide(c):
    """Nettoie et valide un candidat numero de facture."""
    c = c.strip(" :.-#").upper()
    if len(c) < 2:
        return None
    if c.lower() in ("de", "du", "client", "no", "la", "date", "et", "sur", "le"):
        return None
    # un numero de facture n'est pas une date
    if re.fullmatch(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", c):
        return None
    # ni un SIRET (14 chiffres) : erreur de lecture classique
    if re.fullmatch(r"\d{14}", c):
        return None
    return c


def extraire_numero(t):
    """Extrait le numero de facture, formats francais ET anglais, meme quand
    la date est sur la MEME ligne (« Facture N° FA-123 du 12/10/2026 »),
    meme avec les libelles Ref / Piece / Document, et en dernier recours
    un numero isole (FA-123, 2026-307) sur une ligne qui parle de facture."""
    motifs = [
        r"\bfacture\s*(?:n\s*[.o°]|numero|number|#)\s*[:#.\-]?\s*([a-z0-9][a-z0-9./_-]*)",
        r"\bnumero\s+(?:de\s+)?facture\s*[:#.\-]?\s*([a-z0-9][a-z0-9./_-]*)",
        r"\bn\s*[.o°]?\s*(?:de\s+)?facture\s*[:#.\-]?\s*([a-z0-9][a-z0-9./_-]*)",
        r"\bfacture\s*[:#]\s*([a-z0-9][a-z0-9./_-]{2,})",
        r"\bfactures?\s+(\d[\w./-]{2,})",
        r"\binvoice\s*(?:(?:no|number|#)\s*)?[:#.\-]?\s*([a-z0-9][a-z0-9./_-]*)",
        r"\bf\s*/\s*n\s*[:.\-]?\s*([a-z0-9][a-z0-9./_-]+)",
        r"\bn\s*[.o°]?\s*[:\-]\s*([a-z0-9][a-z0-9./_-]{2,})",
        r"\bref(?:erence)?\s*(?:facture|fac)?\s*[:.\-]\s*([a-z0-9][a-z0-9./_-]{2,})",
        r"\b(?:piece|document)\s*(?:n\s*[.o°]?|numero|#)?\s*[:.\-]?\s*([a-z0-9][a-z0-9./_-]{2,})",
        r"\bfac\b\s*[:#.\-]\s*([a-z0-9][a-z0-9./_-]{2,})",
        r"\bfact\b\s*[:#.\-]\s*([a-z0-9][a-z0-9./_-]{2,})",
    ]
    for ligne in t.splitlines():
        ln = norme(ligne)
        if re.search(r"\bsiret\b|\bsiren\b|\bclient\b|\badherent\b|\badhesion\b"
                     r"|\binterlocuteur\b|\bcommande\b|\bbon\b|\blivraison\b|\bcompte\b", ln):
            continue
        for motif in motifs:
            m = re.search(motif, ln)
            if not m:
                continue
            candidat = _numero_valide(m.group(1))
            if candidat:
                return candidat
            # le 1er mot capture etait peut-etre une date : on cherche un
            # numero plus loin sur la meme ligne (ex. « Facture du 12/10 : FA-77 »)
            for m2 in re.finditer(r"\b(?:fa|fac|fact|inv|in)[\-/][a-z0-9][\w./-]*\b", ln):
                c2 = _numero_valide(m2.group(0))
                if c2:
                    return c2
    # Dernier recours : un numero isole « FA-123 », « 2026-307 » sur une ligne
    # qui parle de facture (meme sans libelle « N° »).
    for ligne in t.splitlines():
        ln = norme(ligne)
        if not re.search(r"facture|invoice|\bfac\b|\bdoc", ln):
            continue
        for m in re.finditer(r"\b((?:fa|fac|fact|inv)[-/]?\d{1,8}(?:[-/][a-z0-9]+)*)\b", ln):
            c = _numero_valide(m.group(1))
            if c:
                return c
        for m in re.finditer(r"\b(20\d{2}[-/]\d{2,6}(?:[-/][a-z0-9]+)*)\b", ln):
            avant = ln[:m.start()]
            if "date" in avant or "echeance" in avant:
                continue
            c = _numero_valide(m.group(1))
            if c:
                return c
    return None


def _toutes_les_dates(t):
    toutes = []
    for ligne in t.splitlines():
        for d in extraire_dates_ligne(ligne):
            if d not in toutes:
                toutes.append(d)
    return toutes


def extraire_date_facture(t):
    lignes = t.splitlines()
    etiquettes = (r"date\s*(?:de\s*)?(?:facture|emission|d.emission)", r"invoice\s*date",
                  r"emise?\s*(?:le|:)", r"date\s*facture", r"issued\s*on",
                  # formats reels : « Facture du 12/10/2026 », « Facture 12/10/2026 »
                  r"facture\s+(?:du|de|emise\s+le)?\s*\d{1,2}[/.\-]")
    for i, ligne in enumerate(lignes):
        ln = norme(ligne)
        if any(re.search(e, ln) for e in etiquettes):
            dates = extraire_dates_ligne(ligne)
            if dates:
                return dates[0]
            if i + 1 < len(lignes):
                dates = extraire_dates_ligne(lignes[i + 1])
                if dates:
                    return dates[0]
    # Filet de securite (scans sans libelle) :
    # - une ligne qui parle de facture et contient une date
    # - sinon, si le document n'a qu'UNE date, on la propose
    for ligne in lignes:
        ln = norme(ligne)
        if re.search(r"facture|invoice", ln):
            dates = extraire_dates_ligne(ligne)
            if dates:
                return dates[0]
    toutes = _toutes_les_dates(t)
    if len(toutes) == 1:
        return toutes[0]
    return None


def extraire_date_echeance(t):
    lignes = t.splitlines()
    etiquettes = (r"echeance", r"due\s*date", r"payment\s*due", r"date\s*limite",
                  r"a\s*payer\s*(?:avant|le)", r"paiement\s*(?:avant|le)", r"reglement\s*(?:avant|le)",
                  r"valable\s*jusqu")
    for i, ligne in enumerate(lignes):
        ln = norme(ligne)
        if any(re.search(e, ln) for e in etiquettes):
            dates = extraire_dates_ligne(ligne)
            if dates:
                return dates[-1]
            if i + 1 < len(lignes):
                dates = extraire_dates_ligne(lignes[i + 1])
                if dates:
                    return dates[-1]
    # Filet de securite : si le document a exactement 2 dates, l'echeance
    # est tres souvent la 2e (la 1e etant la date de facture).
    toutes = _toutes_les_dates(t)
    if len(toutes) == 2:
        return toutes[1]
    return None


def extraire_fournisseur(lignes):
    parasites = (r"facture|invoice|total|siret|tva|vat|date|adresse|client|page|tel|www|http|email|@",
                 r"rue|avenue|cite|street|harbor|bp |cedex|sarl|sas|sa\b|eurl|\d{4,}")
    for l in lignes[:15]:
        l = l.strip()
        ln = norme(l)
        if 3 <= len(l) <= 60 and not any(re.search(p, ln) for p in parasites):
            return l
    return None


def extraire_devise(t, texte):
    m = re.search(r"\b(eur|usd|gbp|chf|mad|xof)\b", t) or re.search(r"[€$£]", texte)
    if not m:
        return None
    d = m.group(0).upper() if m.group(0).isalpha() else m.group(0)
    return {"€": "EUR", "$": "USD", "£": "GBP"}.get(d, d)


def taux_tva_multiples(t):
    trouves = []
    for ligne in t.splitlines():
        m = re.search(r"(?:tva|vat|taxe)[^0-9%]{0,25}(\d{1,2}[.,]\d{1,2}|\d{1,2})\s*%", norme(ligne))
        if m:
            v = m.group(1).replace(",", ".")
            if v not in trouves:
                trouves.append(v)
    return trouves

# ============================================================
# 3bis. MOTEUR IA D'OR - analyse profonde avec validation croisee
# ============================================================
# Principe : au lieu de croire UNE etiquette, le moteur collecte TOUS les
# montants candidats (HT, TTC, TVA, tables de TVA) puis teste tous les trios.
# Il ne garde que les trios qui respectent la regle d'or : HT + TVA = TTC.
# Chaque resultat arrive avec un score de confiance et des notes d'analyse.

# Etiquettes avec leur "poids" (10 = tres precis, 3 = vague)
HT_ETIQ = [
    (r"total\s*hors\s*taxe", 10), (r"montant\s*total\s*ht", 9), (r"total\s*ht\b", 9),
    (r"montant\s*ht\b", 7), (r"subtotal", 6), (r"sous[- ]total", 6), (r"\bht\b", 3),
]
TTC_ETIQ = [
    (r"net\s*a\s*payer", 10), (r"total\s*(?:a|à)\s*payer", 10), (r"balance\s*due", 9),
    (r"amount\s*due", 9), (r"total\s*ttc\b", 9), (r"montant\s*ttc\b", 8),
    (r"grand\s*total", 7), (r"total\s*general", 7), (r"total\s*final", 7),
    (r"\bttc\b", 5), (r"\btotal\b", 3),
]
TVA_ETIQ = [
    (r"montant\s*(?:de\s*)?(?:tva|vat|taxe)", 10), (r"total\s*(?:de\s*)?tva", 9),
    (r"\btva\b", 4), (r"\bvat\b", 4), (r"\btaxe\b", 3),
]


def _montants_sur_ligne(ligne):
    """Tous les montants d'une ligne (pourcentages exclus), gauche -> droite."""
    s = re.sub(r"\d{1,2}(?:[.,]\d{1,2})?\s*%", "", ligne)
    vals = []
    for v in re.findall(NOMBRE, s):
        n = _nombre(v)
        if n is not None and abs(n) < 100000000 and n not in vals:
            vals.append(n)
    return vals


def _collecter_candidats(lignes, tableau_etiq):
    """Retourne [(poids, index_ligne, montant)] pour chaque montant candidat."""
    candidats = []
    for i, ligne in enumerate(lignes):
        ln = norme(ligne)
        for etq, poids in tableau_etiq:
            if not re.search(etq, ln):
                continue
            # le "total" vague ne peut pas designer une ligne HT ou TVA
            if etq == r"\btotal\b" and re.search(r"ht\b|hors\s*taxe|tva\b|vat\b|subtotal|sous[- ]total", ln):
                break
            for m in _montants_sur_ligne(ligne):
                candidats.append((poids, i, m))
            break
    return candidats


def _somme_table_tva(lignes):
    """Detecte les lignes de table de TVA ('8,5 %  37,06  3,15') et somme
    les montants TVA (dernier nombre apres le %)."""
    montants, bases = [], []
    for ligne in lignes:
        if "%" not in ligne:
            continue
        idx = ligne.find("%")
        apres = _montants_sur_ligne(ligne[idx + 1:])
        ln = norme(ligne)
        # uniquement si la ligne parle de TVA/taux ou ressemble a une ligne de table
        if re.search(r"tva|vat|taxe|taux|base", ln) or len(apres) >= 2:
            if apres:
                bases.append(apres[0])
                montants.append(apres[-1])
    return montants, bases


def moteur_ia_dor(texte_extraction):
    """Analyse profonde : cherche le TRIO coherent HT + TVA = TTC le mieux
    note. Retourne ht, ttc, tva, taux, confiance, notes."""
    t = norme(texte_extraction)
    lignes = texte_extraction.splitlines()
    notes = []

    # --- Taux de TVA ---
    taux = None
    m = re.search(r"(?:tva|vat|taxe)[^0-9%]{0,25}(\d{1,2}[.,]?\d{0,2})\s*%", t)
    if m:
        try:
            taux = float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    taux_list = taux_tva_multiples(t)
    if len(taux_list) > 1:
        notes.append("Plusieurs taux de TVA sur la facture : " + ", ".join(taux_list) + " %.")

    # --- Collecte des candidats ---
    ht_c = _collecter_candidats(lignes, HT_ETIQ)[:10]
    ttc_c = _collecter_candidats(lignes, TTC_ETIQ)[:12]
    tva_c = _collecter_candidats(lignes, TVA_ETIQ)[:12]
    montants_table, bases_table = _somme_table_tva(lignes)
    if montants_table:
        notes.append("Table de TVA détectée (" + str(len(montants_table)) +
                     " ligne(s)) — somme des montants TVA : " + str(round(sum(montants_table), 2)) + ".")
    # base HT de la table = bon candidat HT aussi
    if bases_table and sum(bases_table) > 0:
        ht_c.append((8, -1, round(sum(bases_table), 2)))
    if montants_table:
        tva_c.append((9, -1, round(sum(montants_table), 2)))

    meilleur = {"score": -1, "ht": None, "ttc": None, "tva": None}

    def essayer(ht, s_ht, ttc, s_ttc, tva, s_tva):
        nonlocal meilleur
        if not ht or not ttc or ht <= 0 or ttc <= 0 or ttc < ht - 0.02:
            return
        if tva is not None and tva < 0:
            return
        # 1) trio complet : HT + TVA = TTC ?
        if tva is not None:
            if abs(ht + tva - ttc) <= max(0.02, ttc * 0.005):
                score = s_ht + s_ttc + s_tva + 30
                if taux:
                    attendu = ht * taux / 100
                    if abs(attendu - tva) <= max(0.02, abs(attendu) * 0.03):
                        score += 10  # le taux confirme la TVA
                if score > meilleur["score"]:
                    meilleur = {"score": score, "ht": ht, "ttc": ttc, "tva": tva}
        # 2) TVA deduite : TTC - HT (verifiee avec le taux si connu)
        else:
            tva_calc = round(ttc - ht, 2)
            if tva_calc > 0:
                ok = True
                if taux:
                    attendu = ht * taux / 100
                    ok = abs(attendu - tva_calc) <= max(0.02, abs(attendu) * 0.03)
                if ok:
                    score = s_ht + s_ttc + 12
                    if score > meilleur["score"]:
                        meilleur = {"score": score, "ht": ht, "ttc": ttc, "tva": tva_calc}

    for (s_ht, _, ht) in ht_c:
        for (s_ttc, _, ttc) in ttc_c:
            for (s_tva, _, tva) in tva_c:
                essayer(ht, s_ht, ttc, s_ttc, tva, s_tva)
            essayer(ht, s_ht, ttc, s_ttc, None, 0)

    # --- 2e tentative : virgules perdues par l'OCR (ex. 2724 lu au lieu de 27,24) ---
    if meilleur["ht"] is None:
        def _etendre(cands):
            extra = []
            for (p, i, v) in cands:
                if v >= 100 and float(v).is_integer():
                    # l'OCR a peut-être collé les centimes au montant
                    extra.append((max(p - 2, 1), i, round(v / 100, 2)))
            return cands + extra
        for (s_ht, _, ht) in _etendre(ht_c):
            for (s_ttc, _, ttc) in _etendre(ttc_c):
                for (s_tva, _, tva) in _etendre(tva_c):
                    essayer(ht, s_ht, ttc, s_ttc, tva, s_tva)
                essayer(ht, s_ht, ttc, s_ttc, None, 0)
        if meilleur["ht"] is not None:
            notes.append("Montants reconstitués : l'OCR avait collé les centimes "
                         "(ex. 2724 lu au lieu de 27,24). Vérifie quand même.")

    if meilleur["ht"] is not None:
        tva_affichee = meilleur["tva"]
        # si les montants ont été reconstitués (virgule OCR), on reste prudent
        confiance = ("à vérifier" if any("reconstitu" in n for n in notes)
                     else "élevée")
        notes.append("Trio comptable validé : " + str(round(meilleur["ht"], 2)) + " + " +
                     str(round(tva_affichee, 2)) + " = " + str(round(meilleur["ttc"], 2)) + " ✔")
        return {"ht": round(meilleur["ht"], 2), "ttc": round(meilleur["ttc"], 2),
                "tva": round(tva_affichee, 2), "taux": taux,
                "confiance": confiance, "notes": notes}

    # --- Aucun trio coherent trouve : extraction par etiquettes (ancienne methode) ---
    ht_old = extraire_montant(t, [r"total\s*hors\s*taxes", r"total\s*ht", r"montant\s*ht",
                                  r"subtotal", r"sous[- ]total", r"\bht\b"])
    ttc_old = extraire_montant(t, [r"net\s*a\s*payer", r"total\s*(?:à|a)\s*payer", r"amount\s*due",
                                   r"balance\s*due", r"total\s*ttc", r"montant\s*ttc",
                                   r"grand\s*total", r"total\s*general", r"\bttc\b", r"\btotal\b"])
    tva_old = None
    if montant_table_local := (sum(montants_table) if montants_table else None):
        tva_old = round(montant_table_local, 2)
    else:
        tva_old = extraire_montant(t, [r"montant\s*(?:de\s*)?(?:tva|vat|taxe)",
                                       r"(?:tva|vat)\b.*montant", r"\btva\b", r"\bvat\b"])
    notes.append("Aucun trio parfait trouvé — extraction par étiquettes, vérifie les montants.")
    return {"ht": ht_old, "ttc": ttc_old, "tva": tva_old, "taux": taux,
            "confiance": "à vérifier", "notes": notes}

# ============================================================
# 4. COHERENCE + DOUBLONS
# ============================================================

def verifier_coherence(ht, tva, ttc, taux):
    alertes = []
    if ht is None or ttc is None:
        alertes.append("Montant HT ou TTC introuvable sur la facture.")
        return alertes
    if tva is not None:
        ecart = abs(ht + tva - ttc)
        if ecart > max(0.02, ttc * 0.005):
            alertes.append("HT + TVA ne donne pas le TTC : écart de " + str(round(ecart, 2)) + ".")
        if taux:
            attendu = ht * taux / 100
            if abs(attendu - tva) > max(0.02, abs(attendu) * 0.02):
                alertes.append("TVA trouvée : " + str(round(tva, 2)) +
                               " mais le taux de " + str(taux) + " % donnerait " + str(round(attendu, 2)) + ".")
    return alertes


def est_doublon(ligne, existantes):
    for e in existantes:
        if (str(e.get("N° facture", "")).strip().upper() == str(ligne.get("N° facture", "")).strip().upper()
                and str(e.get("Fournisseur", "")).lower() == str(ligne.get("Fournisseur", "")).lower()
                and str(ligne.get("N° facture", "")).strip()):
            return True, e
        if (str(e.get("Fournisseur", "")).lower() == str(ligne.get("Fournisseur", "")).lower()
                and str(e.get("Montant TTC", "")) == str(ligne.get("Montant TTC", ""))
                and str(e.get("Date facture", "")) == str(ligne.get("Date facture", ""))
                and str(ligne.get("Montant TTC", "")).strip() and str(ligne.get("Date facture", "")).strip()):
            return True, e
    return False, None

# ============================================================
# 5. SAUVEGARDE : SQLite (local) + SUPABASE (permanente, facultatif)
# ============================================================
# Principe v20 :
#  - Si les Secrets contiennent une configuration Supabase, les factures
#    sont enregistrees DURABLEMENT sur Supabase (elles survivent aux
#    redemarrages et redeploiements de Streamlit Cloud, et sont
#    accessibles depuis n'importe quel appareil).
#  - Sinon, on retombe sur SQLite (fichier factures.db), comme avant.
#  - En cas d'erreur reseau, la facture est sauvegardee en SQLite pour
#    ne JAMAIS la perdre, et un bouton de migration permet de l'envoyer
#    plus tard sur Supabase.
#  - Chaque ligne porte un identifiant d'utilisateur : les donnees sont
#    separees par utilisateur (et Supabase doit activer la RLS, voir guide).

DOSSIER = os.path.dirname(os.path.abspath(__file__))
BDD = os.path.join(DOSSIER, "factures.db")


def _cloud_config():
    """Lit la configuration Supabase depuis les Secrets Streamlit."""
    try:
        conf = st.secrets["factures_ia"]
        url = str(conf["supabase_url"]).strip().rstrip("/")
        key = str(conf["supabase_key"]).strip()
        utilisateur = str(conf.get("utilisateur", "proprietaire")).strip()

        if url and key:
            return url, key, utilisateur
    except Exception as e:
        st.warning(f"Configuration Supabase indisponible : {e}")

    return None

def _cloud_actif():
    """True si Supabase est configure ET que 'requests' est disponible."""
    return HAS_REQUESTS and _cloud_config() is not None


def _sb_requete(methode, chemin, donnees=None, params=None):
    """Appel REST vers Supabase. Retourne la reponse JSON.
    Fait remonter une exception claire en cas de probleme."""
    url, key, utilisateur = _cloud_config()
    entetes = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json"}
    r = requests.request(methode, url + chemin, headers=entetes,
                         json=donnees, params=params, timeout=20)
    if r.status_code >= 400:
        extrait = ""
        try:
            extrait = str(r.json())[:150]
        except Exception:
            extrait = r.text[:150]
        raise RuntimeError("Supabase a repondu %d %s" % (r.status_code, extrait))
    return r.json() if r.content else None


def _cle(ligne):
    """Cle d'identification d'une facture (numero + fournisseur), pour
    dedoublonner lors de la fusion local + cloud et de la migration."""
    return (str(ligne.get("N° facture", "")).upper().strip() + "|" +
            str(ligne.get("Fournisseur", "")).lower().strip())


# ---------- Couche SQLite (locale, comme avant) ----------

def db_connexion():
    return sqlite3.connect(BDD)


def db_creer():
    with db_connexion() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS factures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donnees TEXT NOT NULL,
            date_ajout TEXT NOT NULL)""")


def sqlite_charger():
    db_creer()
    with db_connexion() as c:
        lignes = c.execute("SELECT donnees FROM factures ORDER BY id").fetchall()
    return [json.loads(l[0]) for l in lignes]


def sqlite_ajouter(ligne):
    db_creer()
    with db_connexion() as c:
        c.execute("INSERT INTO factures (donnees, date_ajout) VALUES (?, ?)",
                  (json.dumps(ligne, ensure_ascii=False), datetime.date.today().isoformat()))


def sqlite_remplacer_numero(ancien, nouveau):
    db_creer()
    with db_connexion() as c:
        for (i, donnees_txt) in c.execute("SELECT id, donnees FROM factures").fetchall():
            d = json.loads(donnees_txt)
            if _cle(d) == _cle(ancien):
                c.execute("UPDATE factures SET donnees = ? WHERE id = ?",
                          (json.dumps(nouveau, ensure_ascii=False), i))


def sqlite_vider():
    with db_connexion() as c:
        c.execute("DELETE FROM factures")


def sqlite_supprimer(numero, fournisseur):
    db_creer()
    with db_connexion() as c:
        for (i, donnees_txt) in c.execute("SELECT id, donnees FROM factures").fetchall():
            d = json.loads(donnees_txt)
            if _cle(d) == (str(numero).upper() + "|" + str(fournisseur).lower()):
                c.execute("DELETE FROM factures WHERE id = ?", (i,))


# ---------- Couche Supabase (permanente, si configuree) ----------

def cloud_charger():
    """Toutes les factures de CET utilisateur (jamais celles des autres)."""
    _, _, utilisateur = _cloud_config()
    lignes = _sb_requete("GET", "/rest/v1/factures",
                         params={"utilisateur": "eq." + utilisateur, "order": "id",
                                 "select": "donnees"}) or []
    return [l["donnees"] for l in lignes if isinstance(l.get("donnees"), dict)]


def cloud_ajouter(ligne):
    _, _, utilisateur = _cloud_config()
    _sb_requete(
        "POST",
        "/facture",
        donnees={
            "utilisateur": utilisateur,
            "donnees": ligne,
            "date_ajout": datetime.date.today().isoformat()
        }
    )


def cloud_tout_pour_utilisateur():
    """[(id, donnees)] : id Supabase + contenu, pour modifier/supprimer."""
    _, _, utilisateur = _cloud_config()
    lignes = _sb_requete("GET", "/rest/v1/factures",
                         params={"utilisateur": "eq." + utilisateur,
                                 "select": "id,donnees"}) or []
    return [(l["id"], l["donnees"]) for l in lignes if isinstance(l.get("donnees"), dict)]


def cloud_remplacer_numero(ancien, nouveau):
    for ident, d in cloud_tout_pour_utilisateur():
        if _cle(d) == _cle(ancien):
            _sb_requete("PATCH", "/rest/v1/factures",
                        params={"id": "eq." + str(ident)},
                        donnees={"donnees": nouveau})


def cloud_supprimer(numero, fournisseur):
    cible = str(numero).upper() + "|" + str(fournisseur).lower()
    for ident, d in cloud_tout_pour_utilisateur():
        if _cle(d) == cible:
            _sb_requete("DELETE", "/rest/v1/factures",
                        params={"id": "eq." + str(ident)})


def cloud_vider():
    _, _, utilisateur = _cloud_config()
    _sb_requete("DELETE", "/rest/v1/factures",
                params={"utilisateur": "eq." + utilisateur})


# ---------- FONCTIONS PRINCIPALES (utilisees par l'application) ----------
# Elles choisissent seules ou lire/ecrire : Supabase si configure, sinon
# SQLite. En cas d'erreur reseau on bascule sur SQLite (perte de donnees
# interdite), et la fusion locale evite tout doublon.

def db_charger():
    if _cloud_actif():
        try:
            cloud = cloud_charger()
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]
            cloud = []
        locales = sqlite_charger()
        # Fusion : les factures locales absentes du cloud sont ajoutees
        # (cas d'une facture sauvegardee pendant une panne reseau).
        vues = {_cle(l) for l in cloud}
        fusion = list(cloud)
        for l in locales:
            if _cle(l) not in vues and str(l.get("N° facture", "")).strip():
                fusion.append(l)
        return fusion
    return sqlite_charger()


def db_ajouter(ligne):
    # Toujours une copie locale : c'est ta sauvegarde de secours.
    try:
        sqlite_ajouter(ligne)
    except Exception:
        pass
    if _cloud_actif():
        try:
            cloud_ajouter(ligne)
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]
            st.session_state["a_migrer"] = True


def db_remplacer_numero(ancien, nouveau):
    try:
        sqlite_remplacer_numero(ancien, nouveau)
    except Exception:
        pass
    if _cloud_actif():
        try:
            cloud_remplacer_numero(ancien, nouveau)
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]


def db_vider():
    try:
        sqlite_vider()
    except Exception:
        pass
    if _cloud_actif():
        try:
            cloud_vider()
        except Exception as e:
            st.error(f"Erreur Supabase : {e}")


def db_supprimer(numero, fournisseur):
    try:
        sqlite_supprimer(numero, fournisseur)
    except Exception:
        pass
    if _cloud_actif():
        try:
            cloud_supprimer(numero, fournisseur)
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]


def migrer_vers_supabase():
    """Migration SECURISEE : lit le fichier local factures.db, envoie sur
    Supabase uniquement les factures absentes du cloud, verifie le
    resultat, et NE SUPPRIME JAMAIS le fichier local (il reste en
    sauvegarde de secours). Retourne (envoyees, total_cloud)."""
    if not _cloud_actif():
        return None, None
    cloud = cloud_charger()
    vues = {_cle(l) for l in cloud}
    envoyees = 0
    for l in sqlite_charger():
        if not str(l.get("N° facture", "")).strip():
            continue
        if _cle(l) in vues:
            continue   # deja dans le cloud : aucun doublon
        cloud_ajouter(l)
        vues.add(_cle(l))
        envoyees += 1
    cloud = cloud_charger()
    return envoyees, len(cloud)

# ============================================================
# 6. DESIGN - THEME SOMBRE
# ============================================================

st.set_page_config(page_title="Factures IA", page_icon="🧾", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    .stApp {background: #101319;}
    .block-container {padding-top: 1.4rem; max-width: 1150px; font-family: 'Inter', sans-serif;}
    [data-testid="stHeader"] {background: transparent;}
    .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
    [data-testid="stCaptionContainer"] {color: #d7dde6 !important;}
    section[data-testid="stSidebar"] {background: #161a22; border-right: 1px solid #262c37;}
    section[data-testid="stSidebar"] * {color: #c9d1dc !important;}
    .hero {
        background: linear-gradient(115deg, #232057 0%, #3b34a8 55%, #4a44c4 100%);
        color: #fff; padding: 1.7rem 2.2rem; border-radius: 18px; margin-bottom: 1rem;
        box-shadow: 0 10px 30px rgba(0,0,0,.35);
    }
    .hero h1 {color: #fff; margin: 0; font-size: 1.85rem; font-weight: 800; letter-spacing: -.5px;}
    .hero p {color: #cfd4ff; margin: .45rem 0 0 0; font-size: 1rem;}
    .etapes span {
        display: inline-block; background: #1c2130; color: #a5b4fc; border: 1px solid #2c3350;
        border-radius: 20px; padding: .35rem .95rem; margin: .25rem .28rem 0 0;
        font-weight: 600; font-size: .9rem;
    }
    div[data-testid="stExpander"] {
        border-radius: 14px; border: 1px solid #262c37; background: #161a22;
        box-shadow: 0 2px 10px rgba(0,0,0,.25); overflow: hidden;
    }
    div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary span {color: #e3e8f0 !important;}
    div[data-testid="stExpander"] p, div[data-testid="stExpander"] li {color: #c9d1dc !important;}
    div[data-testid="stMetric"] {
        background: #161a22; border: 1px solid #262c37; border-radius: 14px; padding: .9rem;
        box-shadow: 0 2px 10px rgba(0,0,0,.25);
    }
    div[data-testid="stMetricLabel"] p {color: #93a0b4 !important;}
    div[data-testid="stMetricValue"] {color: #e8e4d8 !important;}
    .stButton > button {
        background: #1c2130; color: #e3e8f0; border: 1px solid #2c3350; border-radius: 10px;
    }
    .stButton > button:hover {border-color: #4a44c4; color: #fff;}
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #4f46e5, #6366f1); color: #fff; border: none;
    }
    .stButton > button[kind="primary"]:hover {background: linear-gradient(135deg, #5d54f0, #7473f5);}
    .stDownloadButton > button {
        background: #1c2130; color: #a5b4fc; border: 1px solid #2c3350; border-radius: 10px;
    }
    .stDownloadButton > button:hover {border-color: #4a44c4; color: #fff;}
    div[data-testid="stFileUploader"] {
        border: 1.5px dashed #3a4166; border-radius: 14px; padding: .7rem; background: #141824;
    }
    div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {background: #161a22;}
    .stTabs [data-baseweb="tab"] {color: #93a0b4; font-weight: 600;}
    .stTabs [data-baseweb="tab-highlight"] {background: #6366f1;}
    .stTabs [data-baseweb="tab"]:hover {color: #d7dde6;}
    hr {border-color: #262c37;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>🧾 Factures IA · IA d'OR 🏆</h1>
  <p>Lit tes factures PDF, teste tous les montants possibles et ne garde que ceux qui respectent la comptabilité.</p>
</div>
<div class="etapes">
  <span>1️⃣ Dépose tes PDF (jusqu'à 200)</span>
  <span>2️⃣ L'IA analyse en profondeur</span>
  <span>3️⃣ Corrige si besoin</span>
  <span>4️⃣ Ajoute (1 par 1 ou tout en lot)</span>
  <span>5️⃣ Exporte / suis les paiements</span>
</div>
""", unsafe_allow_html=True)

# ============================================================
# 7. SIDEBAR
# ============================================================

with st.sidebar:
    st.markdown("### 🧾 Factures IA")
    st.caption("Version 20 · IA d'OR 🏆")
    if _cloud_actif():
        _, _, utilisateur = _cloud_config()
        st.caption("☁️ Sauvegarde permanente active (utilisateur : " + utilisateur + ")")
    else:
        st.caption("💾 Sauvegarde locale (SQLite)")
    if st.session_state.get("erreur_cloud"):
    
                 "gardées en local. Détail : " + str(st.session_state["erreur_cloud"])
        if st.button("🔄 Réessayer maintenant", key="btn_reessayer_cloud"):
            st.session_state.pop("erreur_cloud", None)
            st.rerun()
    st.divider()

    st.markdown("### 🧭 Navigation")
    page = st.radio(
        "Choisir une page",
        ["🏠 Tableau de bord", "📄 Factures PDF", "💰 Paiements",
         "📤 Pour le comptable", "🤖 Assistant", "📋 Récapitulatif"],
        key="navigation_page",
        label_visibility="collapsed",
    )
    st.divider()

    with st.expander("🗂️ Factures de test (clique ici)"):
        st.markdown("Télécharge ces 3 PDF **différents**, puis dépose-les dans l'application :")
        st.download_button("📄 1 : française classique",
                           facture_test_1(), file_name="facture_test_1.pdf", mime="application/pdf",
                           use_container_width=True)
        st.download_button("📄 2 : anglaise (USD)",
                           facture_test_2(), file_name="facture_test_2.pdf", mime="application/pdf",
                           use_container_width=True)
        st.download_button("📄 3 : format piège (8,5 %)",
                           facture_test_3(), file_name="facture_test_3.pdf", mime="application/pdf",
                           use_container_width=True)
    st.divider()

    with st.expander("🏆 C'est quoi l'IA d'OR ?"):
        st.markdown("""
        Un moteur d'analyse **profonde** :

        - 🧮 Il collecte **tous** les montants candidats de la facture (HT, TTC, TVA, tables de TVA).
        - ✔ Il teste **toutes les combinaisons** et ne garde que celles où **HT + TVA = TTC**.
        - 🔢 Il lit les **tables de TVA** (« Taux | Base | Montant ») et fait la somme.
        - 💡 Il peut **déduire la TVA** (TTC − HT) quand elle est invisible, si le taux confirme.
        - 📊 Il affiche un **score de confiance** et ses notes d'analyse.

        **Règle d'or inchangée : jamais inventer.** Si rien n'est sûr, le champ reste à vérifier.
        """)

    with st.expander("🔍 OCR (factures scannées)"):
        if HAS_OCR:
            st.success("✅ OCR actif : les PDF scannés seront lus automatiquement.")
        else:
            st.markdown("**Étape 1/2 — installe les 2 paquets Python** :")
            st.code("py -m pip install pytesseract pillow", language="bash")
            st.markdown("**Étape 2/2 — installe Tesseract** (le moteur OCR) :")
            st.markdown("1. Télécharge : https://github.com/UB-Mannheim/tesseract/wiki")
            st.markdown("2. Installe dans `C:\\Program Files\\Tesseract-OCR`")
            st.markdown("3. Langues : coche **French** (et English)")
            st.markdown("4. **Relance l'application**")

    with st.expander("❓ C'est quoi HT, TVA, TTC ?"):
        st.markdown("""
        - **HT** = *Hors Taxes* : le prix sans la taxe.
        - **TVA** : le % facturé en plus et reversé à l'État.
        - **TTC** = *Toutes Taxes Comprises* : le total à payer.

        🧮 **Règle d'or : HT + TVA = TTC.**
        """)
    with st.expander("🛡️ Où vont mes données ?"):
        if _cloud_actif():
            st.markdown("""**Mode sauvegarde permanente (activé)** :

            - Tes factures validées (et leurs paiements) sont enregistrées dans
            une base **Supabase** en ligne, configurée par **toi** via les
            Secrets Streamlit. Elles survivent aux redémarrages, aux
            redéploiements et sont accessibles depuis plusieurs appareils.
            - Une **copie locale** (fichier `factures.db`) reste sur la machine
            comme sauvegarde de secours.
            - Chaque utilisateur n'accède qu'à **ses propres** factures
            (colonne `utilisateur` + RLS Supabase à activer, voir le guide).
            - Les **PDF originaux** ne sont PAS envoyés en ligne : seul le
            résumé (fournisseur, n°, dates, montants, paiements) part dans
            la base. Ne mets jamais de secret ou mot de passe dans le code
            ou sur GitHub.""")
            if st.button("📤 Envoyer mes factures locales vers la sauvegarde en ligne",
                         key="btn_migrer_sb", use_container_width=True):
                try:
                    envoyees, total = migrer_vers_supabase()
                    if envoyees is None:
                        st.error("Sauvegarde en ligne non configurée.")
                    elif envoyees == 0:
                        st.success("✅ Rien à envoyer : tout est déjà sauvegardé en ligne "
                                   "(" + str(total) + " factures).")
                    else:
                        st.success("✅ " + str(envoyees) + " facture(s) envoyée(s). "
                                   "Vérification : " + str(total) + " facture(s) en ligne au total. "
                                   "Ton fichier local factures.db est conservé intact.")
                    st.session_state["recap"] = db_charger()
                except Exception as e:
                    st.error("Migration interrompue (aucune donnée perdue) : " + str(e)[:200])
        else:
            st.markdown("""Tout est traité **sur la machine où l'application tourne** :
                        les factures validées sont sauvegardées dans le fichier `factures.db`
                        du dossier de l'application, aucun PDF ni montant n'est envoyé ailleurs.

                        ⚠️ **Important — déploiement sur Streamlit Cloud** : l'espace disque de
                        Streamlit Cloud est **effacé à chaque redémarrage ou redéploiement**.
                        Ta base `factures.db` ne survivra donc PAS là-bas. Pour garder tes
                        données durablement : active la **sauvegarde permanente Supabase**
                        (guide dans l'expander ☁️ ci-dessous), ou lance l'application en
                        **local**, ou **exporte régulièrement** tes factures (CSV / Excel
                        dans le Récapitulatif).""")

    with st.expander("☁️ Sauvegarde permanente : mode d'emploi"):
        st.markdown("""
        Pour que tes factures **survivent sur Streamlit Cloud** (et soient
        accessibles depuis ton téléphone ou un autre PC), branche une base
        **Supabase** gratuite. Étapes (une par une) :

        **1.** Crée un compte sur https://supabase.com puis un nouveau projet
        (nom libre, ex. `factures-ia`, mot de passe fort, région Europe).

        **2.** Dans ton projet Supabase → **SQL Editor**, colle ce code et
        clique sur *Run* :""")
        st.code("""create table factures (
  id bigint generated by default as identity primary key,
  utilisateur text not null default 'proprietaire',
  donnees jsonb not null,
  date_ajout date not null default now()
);

alter table factures enable row level security;

create policy "acces proprietaire" on factures
  for all
  using (utilisateur = 'proprietaire');""", language="sql")
        st.markdown("""
        **3.** Dans Supabase → **Settings → API** : copie l'**URL** du projet
        et la clé **anon / public** (jamais la clé `service_role`).

        **4.** Sur Streamlit Cloud → ton app → **Settings → Secrets**, colle :""")
        st.code("""[factures_ia]
supabase_url = "https://TON-PROJET.supabase.co"
supabase_key = "TA-CLE-ANON-PUBLIC"
utilisateur = "proprietaire"
""", language="toml")
        st.markdown("""
        **5.** **Redémarre l'application** : la mention ☁️ « Sauvegarde
        permanente active » apparaît dans la barre latérale, et un bouton
        **📤 d'envoi** de tes factures locales vers le cloud apparaît dans
        l'expander 🛡️ juste au-dessus. Ton fichier `factures.db` local est
        toujours conservé comme sauvegarde de secours.

        **Sécurité** : la clé reste dans les Secrets Streamlit, jamais dans
        le code ni sur GitHub. Avec la RLS activée (étape 2), chaque
        utilisateur ne voit que ses propres factures. Pour ajouter un
        deuxième utilisateur : crée un autre compte Streamlit Cloud avec
        un autre `utilisateur` dans les Secrets et une seconde *policy*
        Supabase.""")

# ============================================================
# 8. TRAITEMENT
# ============================================================

if "recap" not in st.session_state:
    st.session_state["recap"] = db_charger()

CHAMPS = ["Fournisseur", "N° facture", "Date facture", "Date échéance",
          "Montant HT", "Taux TVA (%)", "Montant TVA", "Montant TTC", "Devise", "Payée ?"]
EXPLIQUES = {
    "Fournisseur": "L'entreprise qui a envoyé la facture",
    "N° facture": "Le numéro unique écrit sur la facture",
    "Date facture": "Le jour où la facture a été émise (JJ/MM/AAAA)",
    "Date échéance": "La date limite de paiement (JJ/MM/AAAA)",
    "Montant HT": "Prix hors taxes",
    "Taux TVA (%)": "Le pourcentage de TVA appliqué (ex. 20)",
    "Montant TVA": "Le montant de la taxe",
    "Montant TTC": "Le total à payer, taxes comprises",
    "Devise": "EUR, USD...",
    "Payée ?": "OUI si la facture a été payée, NON sinon",
}


def _val(x):
    try:
        return float(str(x).replace(",", ".")) if str(x).strip() else None
    except ValueError:
        return None


# ---------- SUIVI DES PAIEMENTS (v17) ----------

DOCS = os.path.join(DOSSIER, "documents_factures")


def _nom_pdf(ligne):
    """Nom de fichier sûr pour la copie du PDF (fournisseur + n° facture)."""
    base = (str(ligne.get("Fournisseur", "facture")) + "_" +
            str(ligne.get("N° facture", ""))).strip().replace(" ", "_")
    base = re.sub(r"[^\w.\-]", "_", base) or "facture"
    return base + ".pdf", os.path.join(DOCS, base + ".pdf")


def sauver_pdf(ligne, f):
    """Copie le PDF original dans le dossier 'documents_factures' pour
    pouvoir l'envoyer au comptable plus tard. Échoue silencieusement."""
    try:
        os.makedirs(DOCS, exist_ok=True)
        _, chemin = _nom_pdf(ligne)
        with open(chemin, "wb") as sortie:
            sortie.write(f.getvalue())
    except Exception:
        pass


def _montant_paye(ligne):
    v = _val(ligne.get("Montant payé"))
    return v or 0.0


def _reste_a_payer(ligne):
    ttc = _val(ligne.get("Montant TTC"))
    if ttc is None:
        return None
    if str(ligne.get("Payée ?", "")).upper() == "OUI":
        return 0.0
    return round(max(ttc - _montant_paye(ligne), 0.0), 2)


def _statut_paiement(ligne):
    """JAMAIS 'Payée' sans confirmation humaine : le statut vient du champ
    'Payée ?' que seul l'utilisateur peut mettre à OUI."""
    if str(ligne.get("Payée ?", "")).upper() == "OUI":
        return "Payée"
    if _montant_paye(ligne) > 0:
        return "Partiellement payée"
    return "Non payée"


def _date_depuis_txt(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(d or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


# ---------- MODES DE PAIEMENT (comme un vrai site) ----------

MODES_PAIEMENT = ["🏦 Virement", "💳 Carte bancaire", "✍️ Chèque",
                  "💶 Espèces", "🔁 Prélèvement", "📱 Autre"]


# ---------- ASSISTANT IA (v18) ----------

def repondre_question(question, lignes):
    """Assistant 100 % local : répond UNIQUEMENT avec les factures
    enregistrées. Jamais d'invention, jamais d'accès Internet."""
    if not lignes:
        return ("Aucune facture enregistrée pour l'instant. Ajoute des factures "
                "dans « 📄 Factures PDF » et repose-moi ta question 😉")
    q = norme(question)
    n = len(lignes)
    intro = "D'après tes **" + str(n) + "** facture(s) enregistrée(s) :\n\n"
    tot_ttc = sum(_val(l.get("Montant TTC")) or 0 for l in lignes)
    tot_ht = sum(_val(l.get("Montant HT")) or 0 for l in lignes)
    tot_tva = sum(_val(l.get("Montant TVA")) or 0 for l in lignes)
    tot_paye = sum(_val(l.get("Montant payé")) or 0 for l in lignes)
    reste_total = round(tot_ttc - tot_paye, 2)
    aujourdhui_a = datetime.date.today()

    # Retards
    if "retard" in q:
        ret = []
        for l in lignes:
            if str(l.get("Payée ?", "")).upper() == "OUI":
                continue
            d = _date_depuis_txt(l.get("Date échéance"))
            if d and d < aujourdhui_a:
                ret.append(l)
        if not ret:
            return intro + "✅ Aucune facture en retard de paiement."
        txt = intro + "🚨 **" + str(len(ret)) + "** facture(s) en retard :\n"
        for l in ret:
            txt += ("- " + str(l.get("Fournisseur")) + " n° " + str(l.get("N° facture")) +
                    " — échéance " + str(l.get("Date échéance")) + ", reste " +
                    str(_reste_a_payer(l)) + " " + str(l.get("Devise", "")) + "\n")
        return txt

    # Question sur UN fournisseur précis
    for l in lignes:
        f = str(l.get("Fournisseur", "")).strip()
        if f and len(f) >= 3 and norme(f) in q:
            factures_f = [x for x in lignes if norme(str(x.get("Fournisseur", ""))) == norme(f)]
            ttc_f = sum(_val(x.get("Montant TTC")) or 0 for x in factures_f)
            paye_f = sum(_val(x.get("Montant payé")) or 0 for x in factures_f)
            return (intro + "🏢 **" + f + "** : " + str(len(factures_f)) +
                    " facture(s) · TTC " + str(round(ttc_f, 2)) +
                    " · payé " + str(round(paye_f, 2)) +
                    " · reste " + str(round(ttc_f - paye_f, 2)))

    # Prochaines échéances
    if "prochain" in q or "echeance" in q or "a venir" in q:
        futures = [(d, l) for l in lignes
                   if str(l.get("Payée ?", "")).upper() != "OUI"
                   for d in [_date_depuis_txt(l.get("Date échéance"))] if d and d >= aujourdhui_a]
        futures.sort(key=lambda x: x[0])
        if not futures:
            return intro + "Aucune échéance à venir."
        txt = intro + "⏳ Prochaines échéances :\n"
        for d, l in futures[:5]:
            txt += ("- le " + d.strftime("%d/%m/%Y") + " : " + str(l.get("Fournisseur")) +
                    " n° " + str(l.get("N° facture")) + ", reste " + str(_reste_a_payer(l)) + "\n")
        return txt

    # Moyens de paiement
    if "moyen" in q or "mode" in q or "comment" in q and "paie" in q:
        compte = {}
        for l in lignes:
            for p in (l.get("Historique paiements") or []):
                m_ = str(p.get("mode", "?"))
                compte[m_] = compte.get(m_, 0) + 1
        if not compte:
            return intro + "Aucun paiement enregistré pour l'instant (page « 💰 Paiements »)."
        txt = intro + "💳 Paiements enregistrés, par moyen :\n"
        for m_, nb in sorted(compte.items(), key=lambda x: -x[1]):
            txt += "- " + m_ + " : " + str(nb) + " paiement(s)\n"
        return txt

    # Combien de factures
    if "combien" in q and "facture" in q and "total" not in q and "reste" not in q:
        payees = sum(1 for l in lignes if _statut_paiement(l) == "Payée")
        partielles = sum(1 for l in lignes if _statut_paiement(l) == "Partiellement payée")
        return (intro + "📊 **" + str(n) + "** facture(s) : " + str(payees) +
                " payée(s), " + str(partielles) + " partiellement payée(s), " +
                str(n - payees - partielles) + " non payée(s).")

    # Reste à payer / dette
    if any(m in q for m in ("reste", "dois", "impay", "dette")):
        impayees = [l for l in lignes if _statut_paiement(l) != "Payée"]
        txt = (intro + "💸 **Reste à payer au total : " + str(max(reste_total, 0.0)) +
               "** sur " + str(len(impayees)) + " facture(s) non réglée(s).")
        if impayees:
            txt += "\n\nDétail :\n"
            for l in impayees:
                txt += ("- " + str(l.get("Fournisseur")) + " n° " + str(l.get("N° facture")) +
                        " : reste " + str(_reste_a_payer(l)) + "\n")
        return txt

    # TVA
    if "tva" in q:
        return (intro + "🧮 Total TVA : **" + str(round(tot_tva, 2)) + "** (HT " +
                str(round(tot_ht, 2)) + " → TTC " + str(round(tot_ttc, 2)) + ").")

    # Payé
    if "paye" in q and ("combien" in q or "total" in q or "deja" in q):
        return (intro + "💰 Déjà payé : **" + str(round(tot_paye, 2)) + "** sur " +
                str(round(tot_ttc, 2)) + " facturés (reste " + str(max(reste_total, 0.0)) + ").")

    # Totaux
    if "combien" in q or "total" in q or "montant" in q or "chiffre" in q:
        return (intro + "📊 Total HT : **" + str(round(tot_ht, 2)) + "** · Total TTC : **" +
                str(round(tot_ttc, 2)) + "** · Payé : **" + str(round(tot_paye, 2)) +
                "** · Reste : **" + str(max(reste_total, 0.0)) + "**.")

    # Aide
    return ("Je ne suis pas sûr de comprendre 🤔 Essaie par exemple :\n"
            "- *Combien me reste-t-il à payer ?*\n"
            "- *Quelles factures sont en retard ?*\n"
            "- *Quelles sont les prochaines échéances ?*\n"
            "- *Combien de TVA au total ?*\n"
            "- *Combien dois-je à ENTREPRISE DUPONT ?*\n"
            "- *Par quels moyens ai-je payé ?*")


def traiter_facture(f):
    """Lit un PDF et retourne tout ce qui a ete extrait + le statut + l'analyse IA d'OR."""
    texte, methode = lire_texte_pdf(f)
    if not texte or len(texte) < 40:
        return None  # illisible

    lignes_vis = lignes_par_position(f)
    if lignes_vis and len(lignes_vis) > 3:
        texte_extraction = "\n".join(lignes_vis)
    else:
        texte_extraction = texte
    t = norme(texte_extraction)

    # === MOTEUR IA D'OR ===
    analyse = moteur_ia_dor(texte_extraction)

    donnees = {
        "Fournisseur": extraire_fournisseur(lignes_vis or texte.splitlines()),
        "N° facture": extraire_numero(t),
        "Date facture": extraire_date_facture(t),
        "Date échéance": extraire_date_echeance(t),
        "Montant HT": analyse["ht"],
        "Taux TVA (%)": analyse["taux"],
        "Montant TVA": analyse["tva"],
        "Montant TTC": analyse["ttc"],
        "Devise": extraire_devise(t, texte_extraction),
        "Payée ?": "NON",
    }
    manquants = [c for c in CHAMPS if donnees.get(c) in (None, "")]
    alertes = verifier_coherence(analyse["ht"], analyse["tva"], analyse["ttc"], analyse["taux"])
    return {"donnees": donnees, "manquants": manquants, "alertes": alertes,
            "liste_taux": taux_tva_multiples(t), "methode": methode,
            "confiance": analyse["confiance"], "notes": analyse["notes"],
            "texte": texte_extraction}


# ---------- PAGE : TABLEAU DE BORD ----------

if page == "🏠 Tableau de bord":
    if not st.session_state["recap"]:
        st.info("Aucune facture enregistrée pour le moment. Ouvre « 📄 Factures PDF » dans le menu de gauche pour commencer.")
    else:
        df_dashboard = pd.DataFrame(st.session_state["recap"], columns=CHAMPS)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Factures enregistrées", len(df_dashboard))
        c2.metric("Total HT", str(round(sum(_val(v) or 0 for v in df_dashboard["Montant HT"]), 2)))
        c3.metric("Total TTC", str(round(sum(_val(v) or 0 for v in df_dashboard["Montant TTC"]), 2)))
        c4.metric("Fournisseurs différents", df_dashboard["Fournisseur"].nunique())
        # v18 : vue paiements directement sur le tableau de bord
        tot_paye_d = sum(_val(r.get("Montant payé")) or 0 for r in st.session_state["recap"])
        tot_ttc_d = sum(_val(v) or 0 for v in df_dashboard["Montant TTC"])
        d1, d2, d3 = st.columns(3)
        d1.metric("💰 Déjà payé", str(round(tot_paye_d, 2)))
        d2.metric("💸 Reste à payer", str(max(round(tot_ttc_d - tot_paye_d, 2), 0.0)))
        ajd_d = datetime.date.today()
        retards_d = []
        for r in st.session_state["recap"]:
            if str(r.get("Payée ?", "")).upper() == "OUI":
                continue
            ech_d = _date_depuis_txt(r.get("Date échéance"))
            if ech_d and ech_d < ajd_d:
                retards_d.append(r)
        d3.metric("🚨 En retard", str(len(retards_d)))
        for r in retards_d:
            st.error("🚨 **" + str(r.get("Fournisseur", "?")) + "** n° " +
                     str(r.get("N° facture", "?")) + " — échéance dépassée depuis le " +
                     str(r.get("Date échéance")) + " · reste " + str(_reste_a_payer(r)))
        if not retards_d and tot_ttc_d - tot_paye_d > 0.005:
            st.success("✅ Aucune facture en retard de paiement.")
        st.dataframe(df_dashboard, use_container_width=True, hide_index=True)
        st.caption("Les factures enregistrées survivent à la fermeture de l'application "
                   + ("(sauvegarde permanente Supabase + copie locale factures.db)."
                      if _cloud_actif() else "(fichier factures.db)."))

# ---------- PAGE : FACTURES PDF ----------

fichiers = None
if page == "📄 Factures PDF":
    # Message du dernier ajout en lot (survit au rerun)
    if st.session_state.get("rapport_lot"):
        r = st.session_state.pop("rapport_lot")
        st.success("✅ **" + str(r["ajoutees"]) + " facture(s) ajoutée(s) au récapitulatif.**")
        if r["passees"]:
            st.warning("⏭️ **Non ajoutées automatiquement** (à corriger à la main ou doublons) : "
                       + ", ".join(r["passees"]))

    fichiers = st.file_uploader("📂 Dépose tes factures PDF (jusqu'à 200 à la fois)",
                                type=["pdf"], accept_multiple_files=True,
                                help="PDF numériques ET scannés (si OCR installé). "
                                     "Astuce : sélectionne plusieurs fichiers avec Ctrl+clic dans la fenêtre.")

    if fichiers:
        if len(fichiers) > 200:
            st.warning("⚠️ Plus de 200 fichiers déposés : seuls les 200 premiers seront traités.")

if page == "📄 Factures PDF" and fichiers:

    resultats = []
    for f in fichiers[:200]:
        with st.spinner("🧠 IA d'OR analyse " + f.name + "..."):
            res = traiter_facture(f)
        if res is None:
            resultats.append({"f": f, "illisible": True})
        else:
            resultats.append({"f": f, "illisible": False, **res})

    # --- Resume + bouton d'ajout EN LOT ---
    valides, a_corriger, illisibles = [], [], []
    for r in resultats:
        if r["illisible"]:
            illisibles.append(r)
        elif not r["manquants"] and not r["alertes"]:
            valides.append(r)
        else:
            a_corriger.append(r)

    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Valides", str(len(valides)))
    c2.metric("🟠 À corriger", str(len(a_corriger)))
    c3.metric("🔴 Illisibles", str(len(illisibles)))

    if valides:
        if st.button("➕ Ajouter TOUTES les factures valides (" + str(len(valides)) + ")",
                     type="primary", use_container_width=True, key="btn_lot"):
            ajoutees, passees = 0, []
            recap_actuel = st.session_state["recap"]
            for r in valides:
                ligne = r["donnees"]
                doublon, _ = est_doublon(ligne, recap_actuel)
                if doublon:
                    passees.append(r["f"].name + " (doublon)")
                    continue
                db_ajouter(ligne)
                sauver_pdf(ligne, r["f"])   # v17 : garde le PDF pour le comptable
                recap_actuel = recap_actuel + [ligne]
                ajoutees += 1
            st.session_state["recap"] = db_charger()
            st.session_state["rapport_lot"] = {"ajoutees": ajoutees, "passees": passees}
            st.rerun()
    if a_corriger:
        st.caption("ℹ️ Les factures « à corriger » doivent être complétées/validées une par une dans leur carte ci-dessous.")

    # --- Affichage detaille, fichier par fichier ---
    for r in resultats:
        f = r["f"]
        if r["illisible"]:
            with st.expander("📄 " + f.name + " — 🔴 illisible", expanded=False):
                if HAS_OCR:
                    st.error("Rien à lire dans ce PDF, même avec l'OCR. "
                             "Photo trop sombre, trop penchée ou mauvaise qualité ?")
                else:
                    st.error("Ce PDF est un scan (pas de texte). Installe l'OCR : menu de gauche → « 🔍 OCR ».")
            continue

        donnees, manquants, alertes = r["donnees"], r["manquants"], r["alertes"]
        trouves = 10 - len(manquants)
        if r["confiance"] == "élevée" and not alertes:
            badge = "🟢 **" + str(trouves) + "/10 · IA : confiance élevée**"
        elif not r["manquants"] and not r["alertes"]:
            badge = "🟢 **" + str(trouves) + "/10 · valide**"
        elif trouves >= 4:
            badge = "🟠 **" + str(trouves) + "/10 · à corriger**"
        else:
            badge = "🔴 **" + str(trouves) + "/10 · à corriger**"

        with st.expander("📄 " + f.name + "   " + badge + " · lecture : " + r["methode"],
                         expanded=(len(resultats) == 1)):
            # Notes d'analyse de l'IA d'OR
            if r["notes"]:
                for note in r["notes"]:
                    st.info("🧠 **IA d'OR** : " + note)

            df = pd.DataFrame([donnees], columns=CHAMPS).fillna("")
            df_corrige = st.data_editor(df, num_rows="fixed", key="ed_" + f.name,
                                       use_container_width=True)

            manquants = [c for c, v in zip(CHAMPS, df_corrige.iloc[0]) if str(v).strip() == ""]
            if manquants:
                st.warning("⚠️ **À vérifier avant d'enregistrer** : " + ", ".join(manquants) +
                           ". Le programme ne devine jamais : complète à la main.")
            else:
                st.success("✅ Tous les champs sont remplis.")

            alertes = verifier_coherence(_val(df_corrige.iloc[0]["Montant HT"]),
                                         _val(df_corrige.iloc[0]["Montant TVA"]),
                                         _val(df_corrige.iloc[0]["Montant TTC"]),
                                         _val(df_corrige.iloc[0]["Taux TVA (%)"]))
            if not alertes:
                st.success("✅ **Montants cohérents** : HT + TVA = TTC. Tu peux enregistrer.")
            else:
                for a in alertes:
                    st.error("❌ " + a)
                _ht = _val(df_corrige.iloc[0]["Montant HT"])
                _ttc = _val(df_corrige.iloc[0]["Montant TTC"])
                if _ht is not None and _ttc is not None and _ttc > _ht:
                    st.info("💡 TVA attendue (TTC − HT) = **" + str(round(_ttc - _ht, 2)) +
                            "** — vérifie sur la facture, et corrige le champ « Montant TVA » si besoin.")

            ligne_candidate = {c: df_corrige.iloc[0][c] for c in CHAMPS}
            doublon, existante = est_doublon(ligne_candidate, st.session_state["recap"])
            if doublon:
                st.error("🔁 **Doublon détecté** : une facture de « " + str(existante.get("Fournisseur")) +
                         " » n° " + str(existante.get("N° facture")) +
                         " est déjà enregistrée. Vérifie que ce n'est pas la même.")
                if st.button("♻️ C'est une correction : remplacer l'existante", key="maj_" + f.name):
                    db_remplacer_numero(existante, ligne_candidate)
                    st.session_state["recap"] = db_charger()
                    st.success("Facture mise à jour.")
                    st.rerun()

            if st.button("➕ Ajouter au récapitulatif", key="btn_" + f.name, type="primary",
                         disabled=bool(manquants or alertes)):
                ligne = {c: df_corrige.iloc[0][c] for c in CHAMPS}
                db_ajouter(ligne)
                sauver_pdf(ligne, f)   # v17 : garde le PDF pour le comptable
                st.session_state["recap"] = db_charger()
                st.rerun()

            # Diagnostic : texte lu par l'application
            with st.expander("🔍 Diagnostic — texte lu par l'application"):
                st.caption("Copie-colle ce texte dans notre conversation si un champ est faux : "
                           "je corrige la règle de lecture.")
                st.code(r.get("texte", "")[:4000], language="text")

elif page == "📄 Factures PDF":
    st.info("👆 Dépose une facture PDF — ou ouvre « 🗂️ Factures de test » à gauche pour essayer avec les 3 PDF d'exemple.")

# ---------- PAGE : PAIEMENTS (v17) ----------

if page == "💰 Paiements":
    if not st.session_state["recap"]:
        st.info("Aucune facture enregistrée pour l'instant. Ouvre « 📄 Factures PDF » pour commencer.")
    else:
        lignes_p = st.session_state["recap"]
        tot_ttc = sum(_val(l.get("Montant TTC")) or 0 for l in lignes_p)
        tot_paye = sum(_val(l.get("Montant payé")) or 0 for l in lignes_p)
        aujourdhui_p = datetime.date.today()
        dans_30j = aujourdhui_p + datetime.timedelta(days=30)

        en_retard, bientot = [], []
        for l in lignes_p:
            if str(l.get("Payée ?", "")).upper() == "OUI":
                continue
            d = _date_depuis_txt(l.get("Date échéance"))
            if d and d < aujourdhui_p:
                en_retard.append(l)
            elif d and d <= dans_30j:
                bientot.append(l)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total facturé (TTC)", str(round(tot_ttc, 2)))
        m2.metric("Total déjà payé", str(round(tot_paye, 2)))
        m3.metric("Reste à payer", str(round(tot_ttc - tot_paye, 2)))
        m4.metric("🚨 En retard", str(len(en_retard)))
        m5.metric("⏳ À venir (< 30 j)", str(len(bientot)))

        for l in en_retard[:10]:
            st.error("🚨 **" + str(l.get("Fournisseur", "?")) + "** n° " + str(l.get("N° facture", "?")) +
                     " — échéance dépassée depuis le " + str(l.get("Date échéance")) +
                     " · reste " + str(_reste_a_payer(l)) + " " + str(l.get("Devise", "")))
        for l in bientot[:10]:
            st.warning("⏳ **" + str(l.get("Fournisseur", "?")) + "** n° " + str(l.get("N° facture", "?")) +
                       " — à payer avant le " + str(l.get("Date échéance")) +
                       " · reste " + str(_reste_a_payer(l)) + " " + str(l.get("Devise", "")))

        # --- Enregistrer un paiement (jamais 'payée' sans confirmation) ---
        st.markdown("### 💵 Enregistrer un paiement")
        impayees = [l for l in lignes_p if str(l.get("Payée ?", "")).upper() != "OUI"]
        if not impayees:
            st.success("🎉 Toutes les factures enregistrées sont marquées payées.")
        else:
            libelles = [str(l.get("Fournisseur", "?")) + " · n° " + str(l.get("N° facture", "?")) +
                        " · reste " + str(_reste_a_payer(l)) for l in impayees]
            choix_p = st.selectbox("Facture concernée", libelles, key="sel_paiement")
            cible = impayees[libelles.index(choix_p)]
            reste_p = _reste_a_payer(cible)
            if reste_p is None:
                st.warning("Le montant TTC de cette facture est inconnu : complète-le dans "
                           "« 📋 Récapitulatif » avant d'enregistrer un paiement.")
            else:
                cp1, cp2, cp3, cp4 = st.columns(4)
                montant_p = cp1.number_input("Montant du paiement", min_value=0.0,
                                             max_value=float(reste_p), value=0.0, step=1.0)
                date_p = cp2.date_input("Date du paiement", aujourdhui_p)
                mode_p = cp3.selectbox("Moyen de paiement", MODES_PAIEMENT)
                note_p = cp4.text_input("Note (facultatif)", "")
                if st.button("✅ Enregistrer ce paiement", type="primary",
                             use_container_width=True, key="btn_paiement"):
                    if montant_p <= 0:
                        st.error("Le montant doit être supérieur à 0.")
                    else:
                        maj = dict(cible)
                        payes = list(maj.get("Historique paiements") or [])
                        payes.append({"date": date_p.strftime("%d/%m/%Y"),
                                      "montant": round(montant_p, 2),
                                      "mode": mode_p, "note": note_p})
                        maj["Historique paiements"] = payes
                        maj["Montant payé"] = round(_montant_paye(cible) + montant_p, 2)
                        db_remplacer_numero(cible, maj)
                        st.session_state["recap"] = db_charger()
                        reste_apres = round(reste_p - montant_p, 2)
                        if reste_apres <= 0.005:
                            st.success("Paiement enregistré : la facture est soldée 💰")
                            st.warning("⚠️ Elle n'est **pas encore** marquée « Payée » : "
                                       "c'est TOI qui confirmes ci-dessous.")
                            if st.button("✔️ CONFIRMER : cette facture est entièrement payée",
                                         key="btn_confirm_payee", type="primary"):
                                maj2 = dict(maj)
                                maj2["Payée ?"] = "OUI"
                                db_remplacer_numero(cible, maj2)
                                st.session_state["recap"] = db_charger()
                                st.success("Facture confirmée payée ✔")
                                st.rerun()
                        else:
                            st.success("Paiement enregistré ✔ — reste à payer : " +
                                       str(reste_apres) + " (statut : Partiellement payée)")

        # --- Historique des paiements ---
        st.markdown("### 📜 Historique des paiements")
        avec_hist = [l for l in lignes_p if l.get("Historique paiements")]
        if not avec_hist:
            st.caption("Aucun paiement enregistré pour l'instant.")
        for l in avec_hist:
            intitule = str(l.get("Fournisseur", "?")) + " · n° " + \
                       str(l.get("N° facture", "?")) + " · " + _statut_paiement(l)
            with st.expander("📖 " + intitule):
                st.dataframe(pd.DataFrame(l["Historique paiements"]),
                             use_container_width=True, hide_index=True)
                soldée = (_reste_a_payer(l) == 0 and (_val(l.get("Montant TTC")) or 0) > 0
                          and str(l.get("Payée ?", "")).upper() != "OUI")
                if soldée:
                    st.info("Cette facture est soldée mais pas encore confirmée payée.")
                    if st.button("✔️ CONFIRMER : entièrement payée",
                                 key="confirm_" + str(l.get("N° facture")) + "_" +
                                 str(l.get("Fournisseur"))):
                        maj = dict(l)
                        maj["Payée ?"] = "OUI"
                        db_remplacer_numero(l, maj)
                        st.session_state["recap"] = db_charger()
                        st.rerun()
                # v18 : annuler le dernier paiement (erreur de saisie, chèque refusé...)
                cle_annul = "annuler_" + str(l.get("N° facture")) + "_" + str(l.get("Fournisseur"))
                if l.get("Historique paiements"):
                    if st.button("↩️ Annuler le dernier paiement", key=cle_annul):
                        payes_a = list(l.get("Historique paiements") or [])
                        dernier_a = payes_a.pop()
                        maj_a = dict(l)
                        maj_a["Historique paiements"] = payes_a
                        maj_a["Montant payé"] = round(
                            max(_montant_paye(l) - float(dernier_a.get("montant", 0) or 0), 0.0), 2)
                        # si la facture était confirmée payée, elle redevient à vérifier
                        if str(l.get("Payée ?", "")).upper() == "OUI":
                            maj_a["Payée ?"] = "NON"
                        db_remplacer_numero(l, maj_a)
                        st.session_state["recap"] = db_charger()
                        st.success("Dernier paiement annulé (" + str(dernier_a.get("montant")) +
                                   " " + str(dernier_a.get("mode", "")) + " du " +
                                   str(dernier_a.get("date", "")) + ").")
                        if str(l.get("Payée ?", "")).upper() == "OUI":
                            st.warning("⚠️ La facture est repassée en « NON payée » : "
                                        "confirme son statut quand c'est réglé.")
                        st.rerun()

        # --- Tableau de suivi global ---
        st.markdown("### 📊 Suivi global des paiements")
        suivi = [{"Fournisseur": l.get("Fournisseur", ""),
                  "N° facture": l.get("N° facture", ""),
                  "Date échéance": l.get("Date échéance", ""),
                  "Montant TTC": l.get("Montant TTC", ""),
                  "Statut": _statut_paiement(l),
                  "Payé": round(_montant_paye(l), 2),
                  "Reste à payer": _reste_a_payer(l)} for l in lignes_p]
        st.dataframe(pd.DataFrame(suivi), use_container_width=True, hide_index=True)
        st.caption("🔒 Règle d'or : une facture n'est JAMAIS marquée « Payée » "
                   "automatiquement — c'est toujours ta confirmation.")

# ---------- PAGE : POUR LE COMPTABLE (v17) ----------

if page == "📤 Pour le comptable":
    if not st.session_state["recap"]:
        st.info("Aucune facture enregistrée pour l'instant. Ouvre « 📄 Factures PDF » pour commencer.")
    else:
        st.markdown("### 📤 Préparer l'envoi au comptable")
        st.caption("Sélectionne les factures, choisis le format adapté à son logiciel, "
                   "et télécharge un pack propre et complet.")

        lignes_c = st.session_state["recap"]
        fournisseurs_c = sorted({str(l.get("Fournisseur", "")) for l in lignes_c if l.get("Fournisseur")})
        aujourdhui_c = datetime.date.today()

        fc1, fc2, fc3 = st.columns(3)
        statuts_c = fc1.multiselect("Statuts à inclure",
                                   ["Payée", "Partiellement payée", "Non payée"],
                                   default=["Payée", "Partiellement payée", "Non payée"])
        fournis_c = fc2.multiselect("Fournisseurs", fournisseurs_c, default=fournisseurs_c)
        periode_c = fc3.selectbox("Période",
                                  ["Tout", "Cette année", "3 derniers mois", "Ce mois-ci"])

        retenues_c = []
        for l in lignes_c:
            if _statut_paiement(l) not in statuts_c:
                continue
            if fournis_c and str(l.get("Fournisseur", "")) not in fournis_c:
                continue
            if periode_c != "Tout":
                d = _date_depuis_txt(l.get("Date facture"))
                if d is None:
                    continue
                if periode_c == "Cette année" and d.year != aujourdhui_c.year:
                    continue
                if periode_c == "3 derniers mois" and d < aujourdhui_c - datetime.timedelta(days=92):
                    continue
                if periode_c == "Ce mois-ci" and (d.year, d.month) != (aujourdhui_c.year, aujourdhui_c.month):
                    continue
            retenues_c.append(l)

        def _df_comptable(lignes_sel):
            return pd.DataFrame([{
                "TIERS": l.get("Fournisseur", ""), "PIECE": l.get("N° facture", ""),
                "DATE_PIECE": l.get("Date facture", ""),
                "DATE_ECHEANCE": l.get("Date échéance", ""),
                "MONTANT_HT": l.get("Montant HT", ""), "TAUX_TVA": l.get("Taux TVA (%)", ""),
                "MONTANT_TVA": l.get("Montant TVA", ""), "MONTANT_TTC": l.get("Montant TTC", ""),
                "DEVISE": l.get("Devise", ""),
                "STATUT_PAIEMENT": _statut_paiement(l),
                "MONTANT_PAYE": round(_montant_paye(l), 2),
                "RESTE_A_PAYER": _reste_a_payer(l)} for l in lignes_sel])

        st.markdown("**" + str(len(retenues_c)) + " facture(s) sélectionnée(s)** pour l'envoi.")
        if retenues_c:
            st.dataframe(_df_comptable(retenues_c), use_container_width=True, hide_index=True)
        else:
            st.warning("Aucune facture ne correspond à ces filtres — change la sélection ci-dessus.")

        # --- Export CSV paramétrable (adapté au logiciel du comptable) ---
        st.markdown("#### 📄 Export CSV adapté au logiciel comptable")
        ec1, ec2, ec3, ec4 = st.columns(4)
        separateur_c = ec1.radio("Séparateur", [";", ",", "Tabulation"], horizontal=True)
        encodage_c = ec2.radio("Encodage", ["UTF-8 avec BOM (recommandé Excel)",
                                            "UTF-8 simple"], horizontal=True)
        format_date_c = ec3.radio("Format des dates", ["JJ/MM/AAAA", "AAAA-MM-JJ"],
                                  horizontal=True)
        decimal_c = ec4.radio("Décimales", ["Point (1250.50)", "Virgule (1250,50)"],
                              horizontal=True)
        df_c = _df_comptable(retenues_c)
        if format_date_c == "AAAA-MM-JJ":
            for col_date in ("DATE_PIECE", "DATE_ECHEANCE"):
                df_c[col_date] = df_c[col_date].apply(
                    lambda d: (str(d)[6:10] + "-" + str(d)[3:5] + "-" + str(d)[0:2]
                               if re.match(r"\d{2}/\d{2}/\d{4}", str(d)) else d))
        if decimal_c.startswith("Virgule"):
            for col_num in ("MONTANT_HT", "TAUX_TVA", "MONTANT_TVA", "MONTANT_TTC",
                            "MONTANT_PAYE", "RESTE_A_PAYER"):
                df_c[col_num] = df_c[col_num].apply(
                    lambda v: str(v).replace(".", ",") if str(v) not in ("", "None") else v)
        sep_final = "\t" if separateur_c == "Tabulation" else separateur_c
        csv_c_bytes = df_c.to_csv(index=False, sep=sep_final).encode(
            "utf-8-sig" if "BOM" in encodage_c else "utf-8")
        st.download_button("⬇️ Export CSV comptable (" + str(len(retenues_c)) + " factures)",
                           csv_c_bytes, file_name="export_comptable.csv", mime="text/csv",
                           use_container_width=True, key="dl_cpt_csv")
        st.caption("💡 Astuce : demande à ton comptable quel séparateur et encodage "
                   "SON logiciel préfère. Les colonnes (TIERS, PIECE, MONTANT_TTC...) "
                   "sont les plus répandues ; on ne promet la compatibilité avec aucun "
                   "logiciel en particulier.")

        # --- Pack ZIP : CSV + PDF originaux + récapitulatif ---
        st.markdown("#### 📦 Pack complet (PDF + CSV + récapitulatif)")
        st.caption("Le pack regroupe les PDF originaux des factures sélectionnées, "
                   "le CSV comptable et un fichier LISEZ-MOI qui résume tout. "
                   "Ton comptable reçoit un dossier classé, prêt à saisir.")
        if st.button("🧮 Préparer le pack ZIP", key="btn_zip"):
            buf_zip = io.BytesIO()
            pdfs_inclus, manquants = 0, []
            with zipfile.ZipFile(buf_zip, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("factures_export_comptable.csv", csv_c_bytes)
                resume = "FACTURE IA — Pack pour le comptable\n"
                resume += "Préparé le " + aujourdhui_c.strftime("%d/%m/%Y") + "\n"
                resume += str(len(retenues_c)) + " facture(s)\n"
                resume += "Séparateur CSV : " + separateur_c + "\n\nLISTE DES FACTURES :\n"
                for l in retenues_c:
                    nom_f, chemin_f = _nom_pdf(l)
                    if os.path.exists(chemin_f):
                        # v18 : les PDF sont rangés PAR MOIS dans le pack
                        d_f = _date_depuis_txt(l.get("Date facture"))
                        sous_dossier = "PDF/" + (d_f.strftime("%Y-%m") if d_f else "date_inconnue")
                        with open(chemin_f, "rb") as pf:
                            z.writestr(sous_dossier + "/" + nom_f, pf.read())
                        pdfs_inclus += 1
                    else:
                        manquants.append(str(l.get("Fournisseur", "?")) + " n° " +
                                         str(l.get("N° facture", "?")))
                    resume += ("- " + str(l.get("Fournisseur", "?")) + " | n° " +
                                str(l.get("N° facture", "?")) + " | TTC " +
                                str(l.get("Montant TTC", "?")) + " " +
                                str(l.get("Devise", "")) + " | " + _statut_paiement(l) + "\n")
                if manquants:
                    resume += ("\nPDF manquants (factures ajoutées avant la sauvegarde "
                               "automatique des PDF) :\n")
                    for m_ in manquants:
                        resume += "- " + m_ + "\n"
                z.writestr("0_LISEZ_MOI.txt", resume.encode("utf-8"))
            st.session_state["pack_zip"] = buf_zip.getvalue()
            st.session_state["pack_info"] = {"pdfs": pdfs_inclus, "total": len(retenues_c)}
        if st.session_state.get("pack_zip"):
            info_c = st.session_state["pack_info"]
            st.success("✅ Pack prêt : " + str(info_c["pdfs"]) + " PDF inclus sur " +
                       str(info_c["total"]) + " facture(s).")
            st.download_button("⬇️ Télécharger le pack ZIP",
                               st.session_state["pack_zip"],
                               file_name="pack_comptable_" + aujourdhui_c.strftime("%Y%m%d") + ".zip",
                               mime="application/zip", use_container_width=True, key="dl_zip")
        st.caption("🔒 Tout reste sur ton ordinateur : le pack est fabriqué chez toi, "
                   "rien n'est envoyé sur Internet. C'est toi qui transmets le fichier "
                   "à ton comptable, par le canal que tu choisis.")

# ---------- PAGE : ASSISTANT IA (v18) ----------

if page == "🤖 Assistant":
    st.markdown("### 🤖 Assistant Factures IA")
    st.caption("Pose-moi une question sur **TES** factures enregistrées. "
               "Je réponds uniquement avec tes données : jamais d'invention, "
               "sans jamais rien inventer. Aucune question n'est envoyée sur Internet.")
    suggestions_a = ["Combien me reste-t-il à payer ?", "Quelles factures sont en retard ?",
                     "Quelles sont les prochaines échéances ?", "Combien de TVA au total ?",
                     "Par quels moyens ai-je payé ?", "Total de mes factures ?"]
    sug_cols = st.columns(3)
    for i_a, sugg in enumerate(suggestions_a):
        if sug_cols[i_a % 3].button(sugg, key="sugg_" + str(i_a), use_container_width=True):
            st.session_state["question_ia"] = sugg
            st.rerun()
    question_a = st.text_input("Ta question", key="question_ia",
                               placeholder="Ex : combien dois-je à ENTREPRISE DUPONT ?")
    if question_a.strip():
        reponse_a = repondre_question(question_a, st.session_state["recap"])
        st.markdown(reponse_a)
        st.caption("🧠 Réponse calculée uniquement à partir de tes factures enregistrées. "
                   "En cas de doute, vérifie toujours dans « 📋 Récapitulatif ».")
    else:
        st.info("👆 Choisis une question exemple ou écris la tienne.")

# ---------- PAGE : RECAPITULATIF ----------

if page == "📋 Récapitulatif":
    if not st.session_state["recap"]:
        st.info("Rien pour l'instant. Valide des factures dans « 📄 Factures PDF », elles arriveront ici "
                "— et **elles restent enregistrées même si tu fermes l'application**.")
    else:
        df_recap = pd.DataFrame(st.session_state["recap"], columns=CHAMPS)
        st.markdown("### 📋 " + str(len(st.session_state["recap"])) + " facture(s) enregistrée(s)")
        m1, m2, m3, m4 = st.columns(4)
        tot_ht = sum(_val(v) or 0 for v in df_recap["Montant HT"])
        tot_tva = sum(_val(v) or 0 for v in df_recap["Montant TVA"])
        tot_ttc = sum(_val(v) or 0 for v in df_recap["Montant TTC"])
        impayees = df_recap[df_recap["Payée ?"].astype(str).str.upper() != "OUI"]
        m1.metric("Total HT", str(round(tot_ht, 2)))
        m2.metric("Total TVA", str(round(tot_tva, 2)))
        m3.metric("Total TTC", str(round(tot_ttc, 2)))
        m4.metric("Reste à payer", str(round(sum(_val(v) or 0 for v in impayees["Montant TTC"]), 2)))

        # Alertes de retard de paiement
        aujourdhui = datetime.date.today()
        en_retard = []
        for _, r in impayees.iterrows():
            d = r.get("Date échéance", "")
            m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(d))
            if m:
                try:
                    ech = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    if ech < aujourdhui:
                        en_retard.append(str(r.get("Fournisseur", "?")) + " n° " + str(r.get("N° facture", "?")) +
                                          " — échéance dépassée depuis le " + str(d))
                except ValueError:
                    pass
        for msg in en_retard:
            st.error("🚨 **En retard de paiement** : " + msg)
        if not en_retard and len(impayees) > 0:
            st.success("✅ Aucune facture en retard de paiement.")
        if len(impayees) == 0 and len(df_recap) > 0:
            st.success("🎉 Toutes les factures sont payées !")

        # Barre de recherche
        recherche = st.text_input("🔍 Rechercher (fournisseur, n° de facture, montant...)", key="recherche_recap")
        df_affiche = df_recap.copy()
        # v17 : colonnes de suivi des paiements
        liste_recap = st.session_state["recap"]
        df_affiche["Statut paiement"] = [_statut_paiement(l) for l in liste_recap]
        df_affiche["Montant payé"] = [round(_montant_paye(l), 2) for l in liste_recap]
        df_affiche["Reste à payer"] = [_reste_a_payer(l) for l in liste_recap]
        if recherche.strip():
            masque = df_affiche.astype(str).apply(
                lambda col: col.str.contains(recherche.strip(), case=False, regex=False, na=False)
            ).any(axis=1)
            df_affiche = df_affiche[masque]
            st.caption(str(len(df_affiche)) + " facture(s) trouvée(s) sur " + str(len(df_recap)) + ".")

        # Marquer payee / non payee (confirmation humaine, historique preserve)
        with st.expander("✅ Marquer une facture comme payée / non payée"):
            st.caption("Pour un paiement PARTIEL ou un historique détaillé, utilise la page "
                       "« 💰 Paiements ». Ici, « OUI » = confirmation que TOUT est payé.")
            c_num, c_etat, c_ok = st.columns([2, 1, 1])
            num_paye = c_num.text_input("N° de facture", key="num_paye")
            nouvel_etat = c_etat.selectbox("Nouvel état", ["OUI", "NON"], key="etat_paye")
            if c_ok.button("Mettre à jour", key="btn_paye"):
                cible = [r for r in st.session_state["recap"]
                         if str(r.get("N° facture", "")).upper() == num_paye.strip().upper()]
                if cible:
                    maj = dict(cible[0])
                    maj["Payée ?"] = nouvel_etat
                    if nouvel_etat == "OUI":
                        # confirmation humaine : paiement complet enregistre
                        ttc_paye = _val(maj.get("Montant TTC"))
                        maj["Montant payé"] = (round(ttc_paye, 2) if ttc_paye is not None
                                               else round(_montant_paye(maj), 2))
                    db_remplacer_numero(cible[0], maj)
                    st.session_state["recap"] = db_charger()
                    st.success("Facture " + num_paye + " → " + nouvel_etat)
                    st.rerun()
                elif num_paye.strip():
                    st.warning("Aucune facture avec ce numéro.")

        # Statistiques par fournisseur
        with st.expander("📊 Statistiques par fournisseur"):
            stats = df_recap.groupby("Fournisseur")["Montant TTC"].agg(["count", "sum"])
            stats.columns = ["Nombre de factures", "Total TTC"]
            st.dataframe(stats.sort_values("Total TTC", ascending=False),
                         use_container_width=True)

        # Graphique : factures par mois
        with st.expander("📈 Montant TTC par mois"):
            def _mois(d):
                m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(d))
                if m:
                    return m.group(3) + "-" + m.group(2)
                return None
            df_mois = df_recap.copy()
            df_mois["Mois"] = df_mois["Date facture"].apply(_mois)
            df_mois = df_mois[df_mois["Mois"].notna()]
            if not df_mois.empty:
                par_mois = df_mois.groupby("Mois")["Montant TTC"].apply(
                    lambda s: sum(_val(v) or 0 for v in s))
                st.bar_chart(par_mois)
            else:
                st.caption("Ajoute des factures avec des dates valides pour voir le graphique.")

        st.dataframe(df_affiche, use_container_width=True, hide_index=True)
        st.caption("💡 Besoin d'enlever une ligne ? Utilise la suppression ci-dessous en donnant son numéro de facture.")

        c1, c2, c3 = st.columns(3)
        st.download_button("⬇️ Exporter en CSV", df_recap.to_csv(index=False).encode("utf-8-sig"),
                           file_name="recapitulatif_factures.csv", mime="text/csv",
                           key="dl_csv", use_container_width=True)
        csv_compta = df_recap.copy()
        csv_compta = csv_compta.rename(columns={
            "Fournisseur": "TIERS", "N° facture": "PIECE", "Date facture": "DATE_PIECE",
            "Date échéance": "DATE_ECHEANCE", "Montant HT": "MONTANT_HT",
            "Taux TVA (%)": "TAUX_TVA", "Montant TVA": "MONTANT_TVA",
            "Montant TTC": "MONTANT_TTC", "Devise": "DEVISE", "Payée ?": "REGLE"})
        st.download_button("⬇️ Exporter en CSV comptable (logiciels)",
                           csv_compta.to_csv(index=False, sep=";").encode("utf-8-sig"),
                           file_name="export_comptable.csv", mime="text/csv",
                           key="dl_csv_compta", use_container_width=True)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_recap.to_excel(w, index=False, sheet_name="Factures")
        st.download_button("⬇️ Exporter en Excel", buf.getvalue(), file_name="recapitulatif_factures.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="dl_xlsx", use_container_width=True)

        with c3:
            with st.expander("🗑️ Supprimer / vider"):
                num_suppr = st.text_input("Numéro de facture à supprimer", key="num_suppr")
                if st.button("Supprimer cette facture", key="btn_suppr"):
                    if num_suppr.strip():
                        cible = [r for r in st.session_state["recap"]
                                 if str(r.get("N° facture", "")).upper() == num_suppr.strip().upper()]
                        if cible:
                            db_supprimer(num_suppr.strip(), cible[0].get("Fournisseur", ""))
                            st.session_state["recap"] = db_charger()
                            st.success("Facture " + num_suppr + " supprimée.")
                            st.rerun()
                        else:
                            st.warning("Aucune facture avec ce numéro.")
                    else:
                        st.warning("Écris d'abord le numéro de facture.")
                if st.button("Tout vider", key="btn_vider"):
                    db_vider()
                    st.session_state["recap"] = []
                    st.rerun()

st.caption("Factures IA — version 20 · IA d'OR 🏆 · " + datetime.date.today().strftime("%d/%m/%Y"))
