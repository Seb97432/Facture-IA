# -*- coding: utf-8 -*-
"""FACTURES IA : trios HT/TVA/TTC, OCR fusion multi-lectures
AVEC REDRESSEMENT DES SCANS PENSES, filtre anti-SIRET, suivi des paiements
PRO, pack comptable par mois, export parametrable, ASSISTANT IA local et
COMPTES REELS via Supabase Auth (email + mot de passe) : chaque facture
est liee a l'UUID du compte, regles RLS cote serveur, separation totale
des donnees entre utilisateurs. Sauvegarde permanente + MIGRATION
SECURISEE depuis SQLite (l'ancien fichier factures.db est GARDE).
Regle d'or : jamais inventer, jamais 'payee' sans confirmation humaine.
v28 : LIMITE DE 500 FACTURES PAR COMPTE (garde cote client + policy SQL
cote serveur), ASSISTANT AVEC MEMOIRE CONVERSATIONNELLE (fil affiche,
contexte des 10 derniers echanges, table 'conversations' protegee par
RLS : reprise, suppression, isolation stricte entre comptes), sidebar
allegee (aides deplacees vers la page 'Aide' + compteur de capacite).
v28.1 : CORRECTIF st.chat_input (le parametre 'default' n'existe pas sur
toutes les versions de Streamlit -> TypeError) : une question-exemple
cliquee est traitee directement. Textes de la barre laterale peaufines.
v28.2 : TEXTES SIDEBAR PROFESSIONNELS : titres d'explicateurs
clairs (exemples de test, OCR, legal, confidentialite, comptes),
structure avec sections-titres, renvois internes coherents. Aucune
logique modifiee.
v29 : REFACTO VISUELLE SOBRE (theme sombre minimaliste : fond #0e0f12,
accent bleu discret, plus de degrade hero, KPI epures) + SIDEBAR EN
2 GROUPES ('Pilotage' et 'Aide & exports', via session_state
'page_courante' + on_change — AUCUNE page supprimee) + ASSISTANT
GENERALISTE (prompt systeme reecrit : repond a toute question, n'utilise
les factures QUE si pertinent, honnetete renforcee).
v29.1 : MARQUE CORRIGEE — plus aucune mention d'une autre app : le moteur
s'appelle desormais 'moteur_analyse' (etait moteur_ia_dor), captions et
footer nettoyees. + RAPPEL FIABILITE OCR : un trio HT+TVA=TTC coherant
ne garantit pas que les montants lus sont exacts (caption avant
enregistrement)."""

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
# 3bis. MOTEUR D'ANALYSE FACTURE IA -  profonde avec validation croisee
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


def moteur_analyse(texte_extraction):
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
# ============================================================
# 5. SAUVEGARDE + AUTHENTIFICATION (Supabase Auth)
# ============================================================
# Principe v22 - SECURITE REELLE :
#  - Chaque utilisateur possede un VRAI compte (email + mot de passe)
#    gere par Supabase Auth. A la connexion, Supabase fournit un jeton
#    (token) propre a cet utilisateur.
#  - Chaque facture est liee a l'IDENTIFIANT UNIQUE (UUID) du compte,
#    pas a un nom ni a un email.
#  - Toutes les requetes vers Supabase partent avec CE jeton, et les
#    regles RLS cote serveur n'autorisent l'acces QU'AUX factures de
#    l'utilisateur connecte. Ce n'est pas juste un filtre d'affichage :
#    meme un code modifie ne peut pas lire les factures d'un autre.
#  - Si Supabase n'est pas configure : mode local (comme avant).
#  - SQLite local : colonne 'utilisateur' ajoutee, chaque ligne est
#    marquee par l'UUID de son proprietaire (les vieilles lignes sont
#    'ancien' et revendicables par le bouton de recuperation).

DOSSIER = os.path.dirname(os.path.abspath(__file__))
BDD = os.path.join(DOSSIER, "factures.db")


def _cloud_config():
    """Lit la configuration Supabase dans les Secrets Streamlit.
    Retourne (url, cle_anon) ou None. Les secrets ne sont JAMAIS affiches."""
    try:
        conf = st.secrets["factures_ia"]
        url = str(conf.get("supabase_url", "")).strip().rstrip("/")
        key = str(conf.get("supabase_key", "")).strip()
        if url and key:
            return url, key
    except Exception:
        pass
    return None


def _cloud_actif():
    """True si Supabase est configure ET que 'requests' est disponible."""
    return HAS_REQUESTS and _cloud_config() is not None


# ---------- AUTHENTIFICATION (Supabase Auth) ----------

def auth_connexion(email, motdepasse):
    """Connecte un utilisateur. Retourne (session, None) ou (None, message).
    La session contient le jeton (token) et l'UUID unique du compte."""
    url, key = _cloud_config()
    r = requests.post(url + "/auth/v1/token?grant_type=password",
                      headers={"apikey": key},
                      json={"email": email.strip(), "password": motdepasse},
                      timeout=20)
    if r.status_code != 200:
        return None, "Email ou mot de passe incorrect."
    d = r.json()
    if not d.get("access_token") or not d.get("user", {}).get("id"):
        return None, "Connexion refusée par Supabase."
    return _faire_session(d, email), None


def auth_inscription(email, motdepasse):
    """Cree un compte. Supabase envoie alors un CODE A 6 CHIFFRES
    (aleatoire, different a chaque fois) dans la boite mail.
    On ne se connecte qu'apres avoir entre ce code (auth_verifier_code)."""
    url, key = _cloud_config()
    r = requests.post(url + "/auth/v1/signup",
                      headers={"apikey": key},
                      json={"email": email.strip(), "password": motdepasse},
                      timeout=20)
    if r.status_code != 200:
        try:
            msg = r.json().get("msg") or r.json().get("error_description") or str(r.json())[:120]
        except Exception:
            msg = str(r.status_code)
        return None, "Inscription impossible : " + str(msg)
    d = r.json()
    if d.get("access_token") and d.get("user", {}).get("id"):
        # cas rare : confirmation desactivee cote Supabase -> deja connecte
        return _faire_session(d, email), None
    # compte cree : il faut maintenant le code recu par mail
    return "code_a_confirmer", None


def auth_verifier_code(email, code):
    """Verifie le code a 6 chiffres recu par mail et ouvre la session.
    Retourne (session, None) ou (None, message)."""
    url, key = _cloud_config()
    r = requests.post(url + "/auth/v1/verify",
                      headers={"apikey": key},
                      json={"type": "signup", "email": email.strip(),
                            "token": code.strip()},
                      timeout=20)
    if r.status_code != 200:
        return None, "Code incorrect ou expiré. Regarde bien le mail le plus récent."
    d = r.json()
    if not d.get("access_token") or not d.get("user", {}).get("id"):
        return None, "Confirmation refusée par Supabase."
    return _faire_session(d, email), None


def auth_oublie(email):
    """Mot de passe oublie : Supabase envoie un code de recuperation
    (aleatoire, different a chaque fois) dans la boite mail."""
    url, key = _cloud_config()
    r = requests.post(url + "/auth/v1/recover",
                      headers={"apikey": key},
                      json={"email": email.strip()}, timeout=20)
    if r.status_code != 200:
        return "Envoi impossible pour cette adresse."
    return None


def auth_recuperer(email, code, nouveau_mdp):
    """Verifie le code de recuperation puis definie un NOUVEAU mot de
    passe. Retourne (session, None) ou (None, message)."""
    url, key = _cloud_config()
    r = requests.post(url + "/auth/v1/verify",
                      headers={"apikey": key},
                      json={"type": "recovery", "email": email.strip(),
                            "token": code.strip()},
                      timeout=20)
    if r.status_code != 200:
        return None, "Code incorrect ou expiré. Regarde bien le mail le plus récent."
    d = r.json()
    token = d.get("access_token")
    if not token or not d.get("user", {}).get("id"):
        return None, "Récupération refusée par Supabase."
    # modification du mot de passe avec le jeton de recuperation
    r2 = requests.put(url + "/auth/v1/user",
                       headers={"apikey": key, "Authorization": "Bearer " + token,
                                "Content-Type": "application/json"},
                       json={"password": nouveau_mdp}, timeout=20)
    if r2.status_code != 200:
        return None, "Code accepté mais nouveau mot de passe refusé (trop court ?)."
    session = _faire_session(d, email)
    session["token"] = token   # on garde le jeton de recuperation actif
    return session, None


def _session():
    """La session de l'utilisateur connecte (ou None si personne ne
    l'est). Stockee dans st.session_state, donc propre a CE navigateur."""
    return st.session_state.get("session_utilisateur")


def _faire_session(d, email):
    """Construit l'objet session a partir d'une reponse Supabase Auth.
    On garde le jeton (token), le jeton de RAFRAICHISSEMENT (refresh),
    l'UUID unique et l'email."""
    return {"token": d["access_token"], "refresh": d.get("refresh_token"),
            "id": str(d["user"]["id"]),
            "email": str(d.get("user", {}).get("email", email))}


def _sb_rafraichir_session():
    """Quand le jeton expire (~1 h), Supabase fournit un jeton de
    rafraichissement : on l'utilise pour rester connecte SANS redemander
    le mot de passe. Retourne True si la session a ete renouvelee."""
    url, key = _cloud_config()
    s = _session()
    if not s or not s.get("refresh"):
        return False
    try:
        r = requests.post(url + "/auth/v1/token?grant_type=refresh_token",
                          headers={"apikey": key},
                          json={"refresh_token": s["refresh"]}, timeout=20)
        if r.status_code != 200:
            return False
        d = r.json()
        if not d.get("access_token"):
            return False
        s["token"] = d["access_token"]
        if d.get("refresh_token"):
            s["refresh"] = d["refresh_token"]
        st.session_state["session_utilisateur"] = s
        return True
    except Exception:
        return False


def _uid_courant():
    """UUID unique de l'utilisateur connecte, ou 'local' en mode local."""
    s = _session()
    return s["id"] if s else "local"


def auth_deconnexion():
    st.session_state.pop("session_utilisateur", None)


def _sb_requete(methode, chemin, donnees=None, params=None, jeton=None):
    """Appel REST vers Supabase, TOUJOURS avec le jeton de l'utilisateur
    connecte (jamais une cle privilegiee). Les regles RLS s'appliquent
    cote serveur : meme ce code ne peut pas voir les factures d'un autre."""
    url, key = _cloud_config()
    token = jeton or _session()["token"]
    for essai in (1, 2):
        entetes = {"apikey": key, "Authorization": "Bearer " + token,
                   "Content-Type": "application/json"}
        r = requests.request(methode, url + chemin, headers=entetes,
                             json=donnees, params=params, timeout=20)
        if r.status_code == 401 and essai == 1 and not jeton:
            # jeton expire : on le renouvelle et on REESSAIE une fois
            if _sb_rafraichir_session():
                token = _session()["token"]
                continue
            auth_deconnexion()
            raise RuntimeError("session expiree")
        break
    if r.status_code == 401:
        auth_deconnexion()
        raise RuntimeError("session expiree")
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


# v28 : LIMITE DE 500 FACTURES PAR UTILISATEUR.
# Une "facture" = UNE ligne validee du recapitulatif (fournisseur + numero).
# Le nombre de pages ou de fichiers PDF ne compte PAS : un PDF de 3 pages
# = 1 facture. Garde cote client ici + policy SQL cote serveur (voir le
# mode d'emploi dans la sidebar) : meme une interface modifiee est bloquee.
LIMITE_FACTURES = 500


def limite_atteinte(lignes=None):
    """True si l'utilisateur a deja ses 500 factures."""
    if lignes is None:
        lignes = st.session_state.get("recap") or []
    return len(lignes) >= LIMITE_FACTURES


# ---------- Couche SQLite (locale, chaque ligne marquee par son UUID) ----------

def db_connexion():
    return sqlite3.connect(BDD)


def db_creer():
    with db_connexion() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS factures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donnees TEXT NOT NULL,
            date_ajout TEXT NOT NULL,
            utilisateur TEXT NOT NULL DEFAULT 'ancien')""")
        colonnes = [r[1] for r in c.execute("PRAGMA table_info(factures)").fetchall()]
        if "utilisateur" not in colonnes:
            # ancienne base sans la colonne : ajout sans rien perdre
            c.execute("ALTER TABLE factures ADD COLUMN utilisateur TEXT NOT NULL DEFAULT 'ancien'")


def sqlite_charger(uid):
    db_creer()
    with db_connexion() as c:
        lignes = c.execute("SELECT donnees FROM factures WHERE utilisateur = ? ORDER BY id",
                           (uid,)).fetchall()
    return [json.loads(l[0]) for l in lignes]


def sqlite_ajouter(ligne, uid):
    db_creer()
    with db_connexion() as c:
        c.execute("INSERT INTO factures (donnees, date_ajout, utilisateur) VALUES (?, ?, ?)",
                  (json.dumps(ligne, ensure_ascii=False),
                   datetime.date.today().isoformat(), uid))


def sqlite_remplacer_numero(ancien, nouveau, uid):
    db_creer()
    with db_connexion() as c:
        for (i, donnees_txt) in c.execute(
                "SELECT id, donnees FROM factures WHERE utilisateur = ?", (uid,)).fetchall():
            d = json.loads(donnees_txt)
            if _cle(d) == _cle(ancien):
                c.execute("UPDATE factures SET donnees = ? WHERE id = ?",
                          (json.dumps(nouveau, ensure_ascii=False), i))


def sqlite_vider(uid):
    db_creer()
    with db_connexion() as c:
        c.execute("DELETE FROM factures WHERE utilisateur = ?", (uid,))


def sqlite_supprimer(numero, fournisseur, uid):
    db_creer()
    with db_connexion() as c:
        for (i, donnees_txt) in c.execute(
                "SELECT id, donnees FROM factures WHERE utilisateur = ?", (uid,)).fetchall():
            d = json.loads(donnees_txt)
            if _cle(d) == (str(numero).upper() + "|" + str(fournisseur).lower()):
                c.execute("DELETE FROM factures WHERE id = ?", (i,))


def sqlite_revendiquer(uid):
    """Recuperation des ANCIENNES factures (mode avant comptes) : elles
    sont marquee 'ancien' et deviennent la propriete de l'utilisateur
    connecte. Ne touche jamais les lignes des autres utilisateurs."""
    db_creer()
    with db_connexion() as c:
        n = c.execute("SELECT COUNT(*) FROM factures WHERE utilisateur = 'ancien'").fetchone()[0]
        c.execute("UPDATE factures SET utilisateur = ? WHERE utilisateur = 'ancien'", (uid,))
    return n


# ---------- Couche Supabase (permanente, protegee par RLS) ----------

def cloud_charger():
    """Les factures de l'utilisateur connecte UNIQUEMENT : filtre par son
    UUID et, en plus, RLS cote serveur refuse tout le reste."""
    params = {"utilisateur": "eq." + _uid_courant(), "order": "id", "select": "donnees"}
    lignes = _sb_requete("GET", "/rest/v1/factures", params=params) or []
    return [l["donnees"] for l in lignes if isinstance(l.get("donnees"), dict)]


def cloud_ajouter(ligne):
    _sb_requete("POST", "/rest/v1/factures",
                donnees={"utilisateur": _uid_courant(), "donnees": ligne,
                         "date_ajout": datetime.date.today().isoformat()})


def cloud_tout_pour_utilisateur():
    params = {"utilisateur": "eq." + _uid_courant(), "select": "id,donnees"}
    lignes = _sb_requete("GET", "/rest/v1/factures", params=params) or []
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
    _sb_requete("DELETE", "/rest/v1/factures",
                params={"utilisateur": "eq." + _uid_courant()})


# ---------- FONCTIONS PRINCIPALES (utilisees par l'application) ----------
# Quand Supabase est configure, il faut ETRE CONNECTE : les donnees
# vivent dans le cloud, separees par UUID. Sinon, mode local comme avant.

def db_charger():
    uid = _uid_courant()
    if _cloud_actif():
        if not _session():
            return []
        try:
            cloud = cloud_charger()
        except Exception as e:
            if "session expiree" in str(e):
                auth_deconnexion()
                st.session_state["erreur_cloud"] = "Session expirée : reconnecte-toi."
                return []
            st.session_state["erreur_cloud"] = str(e)[:200]
            cloud = []
        locales = sqlite_charger(uid)
        # Fusion : les factures locales absentes du cloud sont ajoutees
        # (cas d'une facture sauvegardee pendant une panne reseau).
        vues = {_cle(l) for l in cloud}
        fusion = list(cloud)
        for l in locales:
            if _cle(l) not in vues and str(l.get("N° facture", "")).strip():
                fusion.append(l)
        return fusion
    return sqlite_charger(uid)


def db_ajouter(ligne):
    """Ajoute une facture. Retourne True si ajoute, False si la limite de
    500 factures est atteinte (dans ce cas RIEN n'est enregistre)."""
    if limite_atteinte():
        return False
    uid = _uid_courant()
    try:
        sqlite_ajouter(ligne, uid)   # copie locale de secours, marquee a ton UUID
    except Exception:
        pass
    if _cloud_actif() and _session():
        try:
            cloud_ajouter(ligne)
        except Exception as e:
            if "session expiree" not in str(e):
                st.session_state["erreur_cloud"] = str(e)[:200]
    return True


def db_remplacer_numero(ancien, nouveau):
    uid = _uid_courant()
    try:
        sqlite_remplacer_numero(ancien, nouveau, uid)
    except Exception:
        pass
    if _cloud_actif() and _session():
        try:
            cloud_remplacer_numero(ancien, nouveau)
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]


def db_vider():
    uid = _uid_courant()
    try:
        sqlite_vider(uid)   # UNIQUEMENT les lignes de cet utilisateur
    except Exception:
        pass
    if _cloud_actif() and _session():
        try:
            cloud_vider()
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]


def db_supprimer(numero, fournisseur):
    uid = _uid_courant()
    try:
        sqlite_supprimer(numero, fournisseur, uid)
    except Exception:
        pass
    if _cloud_actif() and _session():
        try:
            cloud_supprimer(numero, fournisseur)
        except Exception as e:
            st.session_state["erreur_cloud"] = str(e)[:200]


def migrer_vers_supabase():
    """Migration SECURISEE : envoie sur Supabase uniquement TES factures
    locales absentes du cloud, verifie le resultat, et NE SUPPRIME JAMAIS
    le fichier local. Retourne (envoyees, total_cloud) ou (None, None)."""
    if not (_cloud_actif() and _session()):
        return None, None
    cloud = cloud_charger()
    vues = {_cle(l) for l in cloud}
    envoyees = 0
    for l in sqlite_charger(_uid_courant()):
        if not str(l.get("N° facture", "")).strip():
            continue
        if _cle(l) in vues:
            continue   # deja dans le cloud : aucun doublon
        cloud_ajouter(l)
        vues.add(_cle(l))
        envoyees += 1
    cloud = cloud_charger()
    return envoyees, len(cloud)


# 6. DESIGN - THEME SOMBRE
# ============================================================

st.set_page_config(page_title="Factures IA", page_icon="🧾", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    .stApp {background: #0e0f12;}
    .block-container {padding-top: 1.4rem; max-width: 1150px; font-family: 'Inter', sans-serif;}
    [data-testid="stHeader"] {background: transparent;}
    .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
    [data-testid="stCaptionContainer"] {color: #c9cdd6 !important;}
    section[data-testid="stSidebar"] {background: #15161b; border-right: 1px solid #23252c;}
    section[data-testid="stSidebar"] * {color: #b8bcc8 !important;}
    /* ---- v29 : hero sobre, plus de degrade ---- */
    .hero {
        background: #15161b; color: #e6e8ec;
        border: 1px solid #23252c; border-radius: 12px;
        padding: 1rem 1.4rem; margin-bottom: 1rem;
    }
    .hero h1 {color: #e6e8ec; margin: 0; font-size: 1.5rem; font-weight: 700; letter-spacing: -.3px;}
    .hero p {color: #8b90a0; margin: .35rem 0 0 0; font-size: .95rem;}
    .etapes span {
        display: inline-block; background: #15161b; color: #9fb0f5; border: 1px solid #2a2d38;
        border-radius: 8px; padding: .3rem .8rem; margin: .25rem .25rem 0 0;
        font-weight: 500; font-size: .85rem;
    }
    div[data-testid="stExpander"] {
        border-radius: 12px; border: 1px solid #23252c; background: #15161b;
        overflow: hidden;
    }
    div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary span {color: #d7dae2 !important;}
    div[data-testid="stExpander"] p, div[data-testid="stExpander"] li {color: #b8bcc8 !important;}
    div[data-testid="stMetric"] {
        background: #15161b; border: 1px solid #23252c; border-radius: 12px; padding: .9rem;
    }
    div[data-testid="stMetricLabel"] p {color: #8b90a0 !important;}
    div[data-testid="stMetricValue"] {color: #e6e8ec !important;}
    .stButton > button {
        background: #1b1d24; color: #d7dae2; border: 1px solid #2a2d38; border-radius: 8px;
    }
    .stButton > button:hover {border-color: #5b7cfa; color: #fff;}
    .stButton > button[kind="primary"] {
        background: #5b7cfa; color: #fff; border: none;
    }
    .stButton > button[kind="primary"]:hover {background: #6d8bfb;}
    .stDownloadButton > button {
        background: #1b1d24; color: #9fb0f5; border: 1px solid #2a2d38; border-radius: 8px;
    }
    .stDownloadButton > button:hover {border-color: #5b7cfa; color: #fff;}
    div[data-testid="stFileUploader"] {
        border: 1.5px dashed #33363f; border-radius: 12px; padding: .7rem; background: #121318;
    }
    div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {background: #15161b;}
    .stTabs [data-baseweb="tab"] {color: #8b90a0; font-weight: 600;}
    .stTabs [data-baseweb="tab-highlight"] {background: #5b7cfa;}
    .stTabs [data-baseweb="tab"]:hover {color: #c9cdd6;}
    hr {border-color: #23252c;}

/* ---- v21 : cartes KPI sobres ---- */
.carte-kpi {
    background: #15161b; border: 1px solid #23252c; border-radius: 12px;
    padding: .8rem .9rem; text-align: center; height: 100%;
}
.carte-kpi .kpi-lib {color: #8b90a0; font-size: .78rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: .4px; margin-bottom: .3rem;}
.carte-kpi .kpi-val {color: #e6e8ec; font-size: 1.35rem; font-weight: 700;}
.carte-kpi.vert {border-color: #1f4d33;}   .carte-kpi.vert .kpi-val {color: #57b98a;}
.carte-kpi.orange {border-color: #4d3a1f;} .carte-kpi.orange .kpi-val {color: #c9a45c;}
.carte-kpi.rouge {border-color: #4d201f;}  .carte-kpi.rouge .kpi-val {color: #d97777;}
.carte-kpi.bleu {border-color: #2a2d38;}   .carte-kpi.bleu .kpi-val {color: #9fb0f5;}

/* ---- v29 : sections de la sidebar, style discret ---- */
.section-titre {
    color: #8b90a0; font-weight: 600; font-size: .72rem;
    letter-spacing: 1.2px; text-transform: uppercase;
    margin: .65rem 0 .25rem 0;
    border-bottom: 1px solid #23252c; padding-bottom: .3rem;
}
div[data-testid="stExpander"] summary:hover span {color: #fff !important;}
section[data-testid="stSidebar"] div[role="radiogroup"] label {
    padding: .28rem .5rem; border-radius: 9px; transition: background .15s;
}
section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {background: #1b1d24;}
div[data-testid="stExpander"] {margin-bottom: .45rem;}


/* ---- v21 : confort sur telephone ---- */
@media (max-width: 640px) {
    .block-container {padding: .7rem .8rem 2rem .8rem; max-width: 100%;}
    .hero {padding: 1.1rem 1.2rem; border-radius: 14px;}
    .hero h1 {font-size: 1.35rem;}
    .hero p {font-size: .88rem;}
    .etapes span {font-size: .78rem; padding: .3rem .6rem;}
    .carte-kpi .kpi-val {font-size: 1.15rem;}
    section[data-testid="stSidebar"] {background: #0e0f12;}
}

</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>🧾 Factures IA</h1>
  <p>Dépose tes factures PDF — l'IA les lit, les vérifie et suit tes paiements.</p>
</div>
""", unsafe_allow_html=True)

# ============================================================
# 7. SIDEBAR
# ============================================================

# v28 : charge les factures AVANT la sidebar pour que le compteur de
# capacite soit juste des le premier affichage.
if "recap" not in st.session_state:
    st.session_state["recap"] = db_charger()

with st.sidebar:
    st.markdown("### 🧾 Factures IA")
    st.caption("✨ Lecture automatique des PDF · suivi des paiements · export comptable")
    st.caption("Version 29")

    # ---------- COMPTE : connexion / inscription / deconnexion ----------
    if _cloud_actif():
        if _session():
            st.success("👤 Connecté : **" + _session()["email"] + "**")
            if st.button("🚪 Se déconnecter", key="btn_deconnexion", use_container_width=True):
                auth_deconnexion()
                st.session_state["recap"] = []
                st.rerun()
        else:
            st.markdown("### 🔐 Ton compte")
            onglet_co, onglet_inscrit = st.tabs(["Se connecter", "Créer un compte"])
            with onglet_co:
                etape_oublie = st.session_state.get("oublie_attente")
                if not etape_oublie:
                    email_co = st.text_input("Email", key="email_co")
                    mdp_co = st.text_input("Mot de passe", type="password", key="mdp_co")
                    if st.button("Se connecter", key="btn_connexion", use_container_width=True):
                        if not email_co.strip() or not mdp_co:
                            st.warning("Écris ton email et ton mot de passe.")
                        else:
                            session, err = auth_connexion(email_co, mdp_co)
                            if session:
                                st.session_state["session_utilisateur"] = session
                                st.session_state["recap"] = db_charger()
                                st.rerun()
                            else:
                                st.error(err)
                    if st.button("🔑 Mot de passe oublié ?", key="btn_oublie",
                                 use_container_width=True):
                        if not email_co.strip():
                            st.warning("Écris d'abord ton email juste au-dessus.")
                        else:
                            err = auth_oublie(email_co)
                            if err:
                                st.error(err)
                            else:
                                st.session_state["oublie_attente"] = email_co.strip()
                                st.rerun()
                else:
                    # RECUPERATION : code recu par mail + nouveau mot de passe
                    st.success("🔑 Un **code à 6 chiffres** a été envoyé à **" +
                               etape_oublie + "** (regarde aussi les spams).")
                    code_r = st.text_input("Code reçu par mail", key="code_recup")
                    mdp_n1 = st.text_input("Nouveau mot de passe (8 caractères ou plus)",
                                           type="password", key="mdp_n1")
                    mdp_n2 = st.text_input("Confirme le nouveau mot de passe",
                                           type="password", key="mdp_n2")
                    if st.button("✅ Réinitialiser mon mot de passe",
                                 key="btn_reset_mdp", use_container_width=True,
                                 type="primary"):
                        if not code_r.strip() or not mdp_n1:
                            st.warning("Écris le code et le nouveau mot de passe.")
                        elif mdp_n1 != mdp_n2:
                            st.warning("Les deux mots de passe ne sont pas identiques.")
                        elif len(mdp_n1) < 8:
                            st.warning("Mot de passe trop court (minimum 8).")
                        else:
                            session, err = auth_recuperer(etape_oublie, code_r, mdp_n1)
                            if session:
                                st.session_state.pop("oublie_attente", None)
                                st.session_state["session_utilisateur"] = session
                                st.session_state["recap"] = db_charger()
                                st.success("✅ Mot de passe modifié, tu es connecté.")
                                st.rerun()
                            else:
                                st.error(err)
                    if st.button("❌ Annuler la récupération", key="btn_annuler_oublie",
                                 use_container_width=True):
                        st.session_state.pop("oublie_attente", None)
                        st.rerun()
            with onglet_inscrit:
                etape_inscrit = st.session_state.get("inscription_attente")
                if not etape_inscrit:
                    email_in = st.text_input("Email", key="email_in")
                    mdp_in = st.text_input("Mot de passe (12 caractères ou plus, jamais réutilisé)",
                                           type="password", key="mdp_in")
                    mdp_in2 = st.text_input("Confirme le mot de passe", type="password", key="mdp_in2")
                    if st.button("Créer mon compte", key="btn_inscription", use_container_width=True):
                        if not email_in.strip() or not mdp_in:
                            st.warning("Écris ton email et un mot de passe.")
                        elif mdp_in != mdp_in2:
                            st.warning("Les deux mots de passe ne sont pas identiques.")
                        elif "@" not in email_in:
                            st.warning("Cette adresse email ne semble pas valide.")
                        elif len(mdp_in) < 8:
                            st.warning("Mot de passe trop court (minimum 8, idéalement 12+).")
                        else:
                            session, err = auth_inscription(email_in, mdp_in)
                            if session == "code_a_confirmer":
                                # ETAPE 1 : compte cree, Supabase a envoye le code par mail
                                st.session_state["inscription_attente"] = email_in.strip()
                                st.rerun()
                            elif session:
                                st.session_state["session_utilisateur"] = session
                                st.session_state["recap"] = db_charger()
                                st.rerun()
                            else:
                                st.error(err)
                else:
                    # ETAPE 2 : l'utilisateur entre le code recu par mail
                    st.success("📧 Compte créé ! Un **code à 6 chiffres** vient d'être "
                               "envoyé à **" + etape_inscrit + "** (regarde aussi les "
                               "spams). Il est aléatoire et différent à chaque envoi.")
                    code_in = st.text_input("Entre le code à 6 chiffres reçu par mail",
                                            key="code_in")
                    if st.button("✅ Confirmer mon compte", key="btn_confirmer_code",
                                 use_container_width=True, type="primary"):
                        if not code_in.strip():
                            st.warning("Écris le code reçu par mail.")
                        else:
                            session, err = auth_verifier_code(etape_inscrit, code_in)
                            if session:
                                st.session_state.pop("inscription_attente", None)
                                st.session_state["session_utilisateur"] = session
                                st.session_state["recap"] = db_charger()
                                st.success("🎉 Compte confirmé, bienvenue !")
                                st.rerun()
                            else:
                                st.error(err)
                    c_re1, c_re2 = st.columns(2)
                    if c_re1.button("📧 Renvoyer le code", key="btn_renvoyer_code",
                                    use_container_width=True):
                        try:
                            url_s, key_s = _cloud_config()
                            requests.post(url_s + "/auth/v1/resend",
                                          headers={"apikey": key_s},
                                          json={"type": "signup", "email": etape_inscrit},
                                          timeout=20)
                            st.success("Un nouveau code t'a été envoyé par mail "
                                       "(l'ancien ne fonctionne plus).")
                        except Exception:
                            st.warning("Envoi impossible pour l'instant, réessaie.")
                    if c_re2.button("❌ Annuler", key="btn_annuler_inscrit",
                                    use_container_width=True):
                        st.session_state.pop("inscription_attente", None)
                        st.rerun()
    else:
        st.caption("💾 Mode sans compte : sauvegarde sur cette machine uniquement. "
                   "Crée un compte pour garder tes factures en ligne et les "
                   "retrouver sur tous tes appareils.")
    if st.session_state.get("erreur_cloud"):
        st.error("⚠️ Problème de connexion à la sauvegarde en ligne : les données ont été "
                 "gardées en local. Détail : " + str(st.session_state["erreur_cloud"]))
        if st.button("🔄 Réessayer maintenant", key="btn_reessayer_cloud"):
            st.session_state.pop("erreur_cloud", None)
            st.rerun()
    st.divider()

    st.markdown("### 🧭 Navigation")
    # v28 : compteur de capacite, toujours visible
    nb_factures = len(st.session_state.get("recap") or [])
    reste_f = LIMITE_FACTURES - nb_factures
    st.progress(min(nb_factures / LIMITE_FACTURES, 1.0))
    if reste_f <= 0:
        st.error("🚫 **Capacité pleine : " + str(LIMITE_FACTURES) + "/" +
                 str(LIMITE_FACTURES) + " factures.** Exporte puis supprime " +
                 "d'anciennes factures pour continuer.")
    else:
        st.caption("🗂️ **" + str(nb_factures) + " / " + str(LIMITE_FACTURES) +
                   "** facture(s) enregistrée(s) · " + str(reste_f) +
                   " emplacement(s) libre(s) au maximum")
    st.markdown('<p class="section-titre">Pilotage</p>', unsafe_allow_html=True)

    def _nav_a():
        st.session_state["page_courante"] = st.session_state["navigation_page"]

    def _nav_b():
        st.session_state["page_courante"] = st.session_state["navigation_page_b"]

    if "page_courante" not in st.session_state:
        st.session_state["page_courante"] = "🏠 Tableau de bord"
    page = st.session_state["page_courante"]
    st.radio(
        "Choisir une page",
        ["🏠 Tableau de bord", "📄 Factures PDF", "💰 Paiements",
         "📋 Récapitulatif"],
        key="navigation_page",
        label_visibility="collapsed",
        on_change=_nav_a,
    )
    st.markdown('<p class="section-titre">Aide &amp; exports</p>',
                unsafe_allow_html=True)
    st.radio(
        "Choisir une page (suite)",
        ["🤖 Assistant", "📤 Pour le comptable", "❓ Aide"],
        key="navigation_page_b",
        label_visibility="collapsed",
        on_change=_nav_b,
    )
    st.divider()

    st.markdown('<p class="section-titre">🧪 Essais & lecture des scans</p>',
                unsafe_allow_html=True)

    with st.expander("🧪 Factures d'exemple — teste en 30 secondes"):
        st.markdown("Trois factures **réelles d'exemple**, dont une anglaise (USD) "
                    "et une aux pièges classiques (TVA 8,5 %). Télécharge-les sur "
                    "ton PC, dépose-les dans « 📄 Factures PDF », et regarde "
                    "le moteur d'analyse extraire, vérifier et alerter : **HT + TVA = TTC**, "
                    "champs manquants surlignés, zéro invention.")
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

    with st.expander("🔍 Lecture des factures scannées (OCR)"):
        if HAS_OCR:
            st.success("✅ **OCR actif** — les PDF scannés et photos de factures "
                       "sont lus automatiquement, avec redressement des pages "
                       "penchées et correction des chiffres confondus (O ↔ 0).")
        else:
            st.markdown("""**L'OCR active la lecture des factures scannées**
            (PDF image ou photo, sans texte lisible). Sans lui, seuls les
            PDF texte fonctionnent.

            **🖥️ Sur ton PC (local)** — 3 étapes :
            1. `py -m pip install pytesseract pillow`
            2. Installe Tesseract : https://github.com/UB-Mannheim/tesseract/wiki
            (dans `C:\\Program Files\\Tesseract-OCR`, langues **French** + English)
            3. **Relance l'application**

            **☁️ Sur Streamlit Cloud** — ajoute à la racine du dépôt un fichier
            **`packages.txt`** contenant :""")
            st.code("""tesseract-ocr
tesseract-ocr-fra""", language="text")
            st.markdown("""puis ajoute `pytesseract` et `pillow` dans
            **`requirements.txt`**. Après redéploiement, la mention
            « ✅ OCR actif » doit apparaître ici. Tant qu'elle est absente,
            les PDF scannés ne sont pas lus — les PDF texte, eux,
            fonctionnent toujours.""")

    st.markdown('<p class="section-titre">⚖️ Transparence : légal, données & comptes</p>',
                unsafe_allow_html=True)

    with st.expander("⚖️ Mentions légales & confidentialité (lisez-moi)"):
        st.markdown("""**⚠️ Ce qu'est Facture IA (et ce qu'elle n'est PAS)**

        Facture IA est un **outil d'aide à la saisie et au suivi des factures
        reçues** (fournisseurs, paiements, exports). Ce n'est **ni un logiciel
        comptable certifié, ni une plateforme agréée** de facturation
        électronique au sens de l'article 289 bis du code général des
        impôts. Les informations affichées ne remplacent pas les conseils
        d'un expert-comptable.

        ---

        **📄 Mentions légales**

        - **Éditeur** : Sébastien LESQUELIN — [À COMPLÉTER : adresse, SIREN
          ou numéro de micro-entreprise le cas échéant]
        - **Contact** : [À COMPLÉTER : ton email de contact]
        - **Hébergement de l'application** : Streamlit Cloud (Snowflake Inc.)
        - **Hébergement des données** : Supabase (région Europe)

        ---

        **🔒 Politique de confidentialité (RGPD)**

        - **Données traitées** : ton adresse email, un mot de passe (jamais
        stocké en clair : il est chiffré par Supabase Auth), et le **résumé
        de tes factures** (fournisseur, n° de facture, dates, montants,
        paiements). **Les PDF originaux ne sont jamais envoyés en ligne** :
        ils restent uniquement sur ta propre machine.
        - **Finalité** : te permettre de gérer tes factures. Aucune donnée
        n'est vendue, louée ou partagée avec des tiers.
        - **Durée de conservation** : jusqu'à ta suppression des données
        (bouton « Tout vider » dans le Récapitulatif) ou ta demande.
        - **Sous-traitants techniques** : Supabase (base de données et
        comptes, région Europe) et Streamlit Cloud (hébergement du site).
        - **Sécurité** : chaque compte n'accède qu'à ses propres données,
        vérifié côté serveur (Supabase Auth + règles RLS).
        - **Tes droits** (accès, rectification, export, suppression) :
        exporte tes factures (CSV / Excel dans le Récapitulatif), supprime-les
        (« Tout vider »), ou écris à [À COMPLÉTER : ton email de contact].

        ---

        **🧪 Cadre du test pilote**

        Accès sur invitation, nombre d'utilisateurs limité. En cas de
        problème : [À COMPLÉTER : ton email]. Note bien : les données
        saisies pendant le pilote sont **tes données** — fais des essais
        avec de vraies copies mais garde tes factures importantes
        sauvegardées ailleurs (export CSV).""")

    with st.expander("🛡️ Confidentialité & sécurité des données"):
        if _cloud_actif():
            st.markdown("""**Sauvegarde en ligne : ACTIVE ✅** — voici exactement
            ce qui part en ligne, et ce qui reste chez toi :

            - **Ce qui est enregistré en ligne** : uniquement le **résumé** de tes
            factures validées (fournisseur, n°, dates, montants, paiements) dans
            une base **Supabase** (Europe). Tes données survivent aux
            redémarrages, aux redéploiements et te suivent sur tous tes appareils.
            - **Ce qui ne quitte JAMAIS ta machine** : les **PDF originaux**.
            Ils sont analysés localement puis supprimés du serveur d'hébergement.
            - **Isolation par compte** : chaque facture est liée à l'identifiant
            unique (UUID) de TON compte. Les règles RLS de Supabase, vérifiées
            **côté serveur**, empêchent tout autre compte de lire, modifier ou
            supprimer tes factures — et t'empêchent d'accéder à celles des autres.
            - **Double sécurité** : une **copie locale** (`factures.db`) reste
            sur la machine, marquée à ton UUID, comme sauvegarde de secours.
            - **Bonne pratique** : ne mets jamais de secret ni de mot de passe
            dans le code ou sur GitHub. Les clés vivent dans les Secrets
            Streamlit, jamais dans l'application.""")
            if _session():
                if st.button("📤 Envoyer mes factures locales vers la sauvegarde en ligne",
                             key="btn_migrer_sb", use_container_width=True):
                    try:
                        envoyees, total = migrer_vers_supabase()
                        if envoyees is None:
                            st.error("Connecte-toi d'abord.")
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
                st.caption("Connecte-toi pour voir le bouton d'envoi de tes factures.")
        else:
            st.markdown("""**Mode 100 % local** — tout est traité **sur la machine
                        où l'application tourne** : les factures validées sont
                        sauvegardées dans le fichier `factures.db` du dossier de
                        l'application. Aucun PDF, aucun montant, aucune donnée
                        n'est envoyé ailleurs.

                        ⚠️ **À savoir si l'app est déployée sur Streamlit Cloud** :
                        l'espace disque de Streamlit Cloud est **effacé à chaque
                        redémarrage ou redéploiement** — ta base `factures.db` ne
                        survivra donc PAS là-bas. Trois solutions, au choix :

                        - Active la **sauvegarde permanente Supabase** (guide
                        complet dans l'expander ☁️ ci-dessous) ;
                        - Lance l'application **en local** sur ta machine ;
                        - **Exporte régulièrement** tes factures (CSV / Excel
                        dans le Récapitulatif).""")

    with st.expander("☁️ Activer les comptes & la sauvegarde en ligne — guide pas à pas"):
        st.markdown("""
        Objectif : que tes factures **survivent sur Streamlit Cloud**, avec de
        **vrais comptes séparés** (email + mot de passe + code de confirmation
        à 6 chiffres). Pas besoin d'être développeur : chaque étape est
        indépendante, aucune donnée n'est perdue en cours de route.

        **1.** Crée un compte sur https://supabase.com puis un nouveau projet
        (nom libre, ex. `factures-ia`, mot de passe fort, région Europe).

        **2.** Dans Supabase → **Authentication → Providers** : vérifie que
        **Email** est activé (c'est le cas par défaut). Puis dans
        **Authentication → Sign Ups**, laisse **« Confirm email » ACTIVÉ** :
        c'est ce qui envoie le code à 6 chiffres à chaque inscription.
        Vérifie aussi dans **Authentication → Email Templates** →
        « Confirm signup » que le message contient `{{ .Token }}`
        (le code) — c'est le cas par défaut. Ne mets jamais
        `{{ .ConfirmationURL }}`... si tu préfères le code, garde `{{ .Token }}`.

        **3.** Dans **SQL Editor**, colle ce code et clique sur *Run* :""")
        st.code("""create table if not exists factures (
  id bigint generated by default as identity primary key,
  utilisateur text not null,
  donnees jsonb not null,
  date_ajout date not null default now()
);

alter table factures enable row level security;

drop policy if exists "acces proprietaire" on factures;

create policy "chacun les siennes" on factures
  for all
  using (auth.uid()::text = utilisateur)
  with check (auth.uid()::text = utilisateur);

-- v28 : LIMITE DE 500 FACTURES PAR COMPTE, appliquee COTE SERVEUR.
-- Une insertion au-dela de 500 est refusee par Supabase lui-meme :
-- modifier l'application ne suffit pas a contourner la limite.
drop policy if exists "limite_500_factures" on factures;

create policy "limite_500_factures" on factures
  for insert to authenticated
  with check (
    (select count(*) from factures f
     where f.utilisateur = auth.uid()::text) < 500
  );

-- v28 : MEMOIRE DE L'ASSISTANT (table 'conversations', protegee par RLS :
-- chaque compte ne voit et ne modifie que SA conversation).
create table if not exists conversations (
  id bigint generated by default as identity primary key,
  utilisateur text not null unique,
  messages jsonb not null default '[]'::jsonb,
  maj timestamptz not null default now()
);

alter table conversations enable row level security;

drop policy if exists "conversation privee" on conversations;

create policy "conversation privee" on conversations
  for all
  using (auth.uid()::text = utilisateur)
  with check (auth.uid()::text = utilisateur);""", language="sql")
        st.markdown("""
        ⚠️ **Si tu avais déjà des factures en ligne** (mode avant comptes,
        marquées `proprietaire`) : elles ne peuvent pas être réattribuées
        depuis l'application. Colle ce code dans SQL Editor **après ta
        première inscription** (remplace TON_EMAIL) — c'est ta migration :""")
        st.code("""update factures
set utilisateur = (select id from auth.users where email = 'TON_EMAIL')
where utilisateur = 'proprietaire';""", language="sql")
        st.markdown("""
        **4.** Dans Supabase → **Settings → API** : copie l'**URL** du projet
        et la clé **anon / public** (jamais la clé `service_role`).

        **5.** Sur Streamlit Cloud → ton app → **Settings → Secrets**, colle :""")
        st.code("""[factures_ia]
supabase_url = "https://TON-PROJET.supabase.co"
supabase_key = "TA-CLE-ANON-PUBLIC"
""", language="toml")
        st.markdown("""
        **6.** **Redémarre l'application** : la barre latérale propose
        « Se connecter » et « **Créer un compte** ». Crée ton compte, puis
        utilise le bouton **📤** (expander 🛡️) pour envoyer tes factures
        locales vers ton espace en ligne. Ton fichier `factures.db` local
        est toujours conservé comme sauvegarde de secours.

        **Sécurité** : la clé anon reste dans les Secrets Streamlit, jamais
        dans le code ni sur GitHub. Grâce à Supabase Auth + RLS, chaque
        compte ne voit **que ses propres factures** — même si quelqu'un
        modifie le code de l'application, le serveur Supabase refuse.

        ---

        **🧠 Activer l'assistant IA complet (facultatif)** : sur
        https://platform.mistral.ai → crée un compte → **API Keys** →
        génère une clé. Puis ajoute-la dans les Secrets Streamlit, sous
        `[factures_ia]` :

        ```
        mistral_api_key = "TA-CLE-MISTRAL"
        ```

        L'assistant 🤖 répond alors comme une vraie IA (questions sur tes
        factures ET questions générales), avec repli automatique sur le
        moteur local en cas de panne. ⚠️ Transparence : pour répondre sur
        tes factures, leurs données sont envoyées à l'API Mistral (c'est
        affiché sur la page Assistant). L'API est payante à l'usage mais
        très peu coûteuse à ton volume ; sans clé, l'assistant local
        fonctionne toujours, sans aucun envoi.""")

# ============================================================
# 8. TRAITEMENT
# ============================================================

# ---------- PORTE D'ENTREE : connexion obligatoire si Supabase est configure ----------
# Tant que l'utilisateur n'est pas connecte, AUCUNE donnee n'est affichee :
# on ne peut donc jamais voir, meme par accident, les factures d'un autre.
if _cloud_actif() and not _session():
    st.markdown("### 🔐 Connecte-toi pour accéder à tes factures")
    st.markdown("""Cette application utilise de **vrais comptes** : chaque utilisateur ne voit,
    ne modifie et ne supprime que **ses propres factures** — c'est vérifié côté serveur
    par Supabase (règles RLS), pas seulement à l'affichage.

    👉 Dans la barre latérale (menu de gauche) : **Se connecter** ou **Créer un compte**.""")
    st.info("💾 Mode local disponible : si la sauvegarde en ligne n'est pas configurée "
            "(Secrets), l'application fonctionne sans compte, comme avant.")
    st.stop()

# ---------- RECUPERATION des anciennes factures (mode avant comptes) ----------
if _session() and st.session_state.get("proposer_recup") is None:
    try:
        anciennes = 0
        db_creer()
        with db_connexion() as c:
            anciennes = c.execute(
                "SELECT COUNT(*) FROM factures WHERE utilisateur = 'ancien'").fetchone()[0]
        st.session_state["proposer_recup"] = anciennes
    except Exception:
        st.session_state["proposer_recup"] = 0

if _session() and st.session_state.get("proposer_recup"):
    with st.expander("🎁 Récupérer mes anciennes factures (" +
                     str(st.session_state["proposer_recup"]) + ")"):
        st.markdown("Tes factures enregistrées **avant la création des comptes** sont "
                    "conservées. Elles appartiennent maintenant à ton compte.")
        c_r1, c_r2 = st.columns(2)
        if c_r1.button("🎁 Les récupérer", key="btn_recup", use_container_width=True):
            try:
                n = sqlite_revendiquer(_uid_courant())
                st.session_state["proposer_recup"] = 0
                st.session_state["recap"] = db_charger()
                st.success("✅ " + str(n) + " facture(s) récupérée(s) sur ce compte. "
                           "Utilise ensuite le bouton 📤 pour les envoyer en ligne.")
                st.rerun()
            except Exception as e:
                st.error("Récupération impossible : " + str(e)[:200])
        if c_r2.button("❌ Les ignorer", key="btn_recup_non", use_container_width=True):
            st.session_state["proposer_recup"] = 0
            st.rerun()

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


# ---------- ASSISTANT IA : VRAIE IA (Mistral) + repli local (v27) ----------
# v28 : MEMOIRE CONVERSATIONNELLE. Le fil est garde en session ET (facultatif)
# dans la table 'conversations' de Supabase, protegee par RLS : chaque compte
# ne voit QUE sa conversation. L'historique complet n'est JAMAIS envoyé à
# Mistral : seuls les 10 derniers échanges partent (fenêtre glissante), et
# rien ne part du tout si l'assistant local est utilisé.

MAX_MESSAGES_STOCKES = 30     # conservés en ligne (anti-base infinie)
MAX_MESSAGES_CONTEXTE = 10    # envoyés à l'IA comme contexte


def cloud_conversation_charger():
    """La conversation sauvegardee de l'utilisateur connecte (ou [] si aucune,
    pas de session, ou table absente). RLS : uniquement la sienne."""
    if not (_cloud_actif() and _session()):
        return []
    try:
        params = {"utilisateur": "eq." + _uid_courant(), "select": "messages"}
        lignes = _sb_requete("GET", "/rest/v1/conversations", params=params) or []
        msgs = (lignes[0] or {}).get("messages") or []
        return msgs if isinstance(msgs, list) else []
    except Exception:
        return []   # table absente ou reseau : silencieux, session seulement


def cloud_conversation_sauver(messages):
    """Enregistre le fil (1 ligne par utilisateur, mise a jour si existe).
    Silencieux en cas d'echec : la conversation reste en session."""
    if not (_cloud_actif() and _session()):
        return
    try:
        params = {"utilisateur": "eq." + _uid_courant()}
        existe = _sb_requete("GET", "/rest/v1/conversations",
                             params={"utilisateur": "eq." + _uid_courant(),
                                     "select": "id"}) or []
        if existe:
            _sb_requete("PATCH", "/rest/v1/conversations",
                        donnees={"messages": messages}, params=params)
        else:
            _sb_requete("POST", "/rest/v1/conversations",
                        donnees={"utilisateur": _uid_courant(), "messages": messages})
    except Exception:
        pass


def cloud_conversation_supprimer():
    """Efface la conversation en ligne de l'utilisateur (uniquement la sienne)."""
    if not (_cloud_actif() and _session()):
        return
    try:
        _sb_requete("DELETE", "/rest/v1/conversations",
                    params={"utilisateur": "eq." + _uid_courant()})
    except Exception:
        pass


def _cle_mistral():
    """Cle API Mistral, facultative, rangee dans les Secrets Streamlit.
    Si absente : l'assistant reste le moteur local (aucune donnee envoyee)."""
    try:
        return str(st.secrets["factures_ia"].get("mistral_api_key", "")).strip()
    except Exception:
        return ""


def repondre_question_ia(question, lignes, historique=None):
    """Assistant IA complet (API Mistral). Retourne (reponse, None) ou
    (None, raison_echec) — dans ce cas on retombe sur le moteur local.
    REGLES D'OR : les questions sur les factures sont repondues UNIQUEMENT
    avec les donnees fourni ; l'IA peut aussi repondre aux questions
    generales. Les factures sont envoyees a l'API Mistral pour fabriquer
    la reponse (c'est affiche clairement a l'utilisateur).
    v28 : MEMOIRE — les MAX_MESSAGES_CONTEXTE derniers échanges sont
    envoyés avec la question, pour que l'IA comprenne les questions de
    suivi (« et la deuxième ? », « pourquoi ce montant ? »...)."""
    cle = _cle_mistral()
    if not cle:
        return None, "pas_de_cle"
    if not HAS_REQUESTS:
        return None, "pas_de_reseau"
    resume = json.dumps(lignes, ensure_ascii=False)[:12000]
    systeme = ("Tu es l'assistant IA intégré de l'application Facture IA — "
               "un assistant conversationnel GENERALISTE, pas un simple "
               "module de comptabilité. Règles : "
               "(1) COMPORTEMENT GENERAL : réponds naturellement à toute "
               "question (explications, rédaction de textes, résumés, "
               "idées, résolution de problèmes, réflexion sur un projet). "
               "Ne ramène JAMAIS artificiellement une question hors "
               "comptabilité vers les factures. Sers-toi de l'historique "
               "pour les questions de suivi et les références implicites. "
               "(2) COMPETENCE SPECIALE : pour toute question liée aux "
               "factures de l'utilisateur (montants, fournisseurs, "
               "échéances, totaux, paiements, relances), utilise "
               "UNIQUEMENT les données JSON fournies ; si une information "
               "est absente ou incertaine, dis-le clairement et ne "
               "l'invente JAMAIS. Propose des montants exacts issus des "
               "données quand c'est pertinent. "
               "(3) HONNETETE : si tu ne connais pas la réponse ou si "
               "l'information manque, dis-le franchement. "
               "(4) FORME : français, réponses bien structurées (titres "
               "courts ou listes quand utile), calibrées en longueur sur "
               "la demande — simple si la question est simple, "
               "approfondie si on te le demande.")
    messages = [{"role": "system", "content": systeme +
                 "\nFACTURES DE L'UTILISATEUR (JSON) :\n" + resume}]
    for m in (historique or [])[-MAX_MESSAGES_CONTEXTE:]:
        if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip():
            messages.append({"role": m["role"], "content": str(m["content"])})
    messages.append({"role": "user", "content": question})
    try:
        r = requests.post("https://api.mistral.ai/v1/chat/completions",
                          headers={"Authorization": "Bearer " + cle,
                                   "Content-Type": "application/json"},
                          json={"model": "mistral-small-latest",
                                "messages": messages},
                          timeout=30)
        if r.status_code != 200:
            return None, "api_" + str(r.status_code)
        return r.json()["choices"][0]["message"]["content"], None
    except Exception as e:
        return None, str(e)[:80]

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
    """Lit un PDF et retourne tout ce qui a ete extrait + le statut + l'analyse du moteur."""
    texte, methode = lire_texte_pdf(f)
    if not texte or len(texte) < 40:
        return None  # illisible

    lignes_vis = lignes_par_position(f)
    if lignes_vis and len(lignes_vis) > 3:
        texte_extraction = "\n".join(lignes_vis)
    else:
        texte_extraction = texte
    t = norme(texte_extraction)

    # === MOTEUR D'ANALYSE ===
    analyse = moteur_analyse(texte_extraction)

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

        def _fm(x):
            """Montant lisible : 1 234,56 (arrondi a 2 decimales)."""
            try:
                return "{:,.2f}".format(round(float(x), 2)).replace(",", " ").replace(".", ",")
            except Exception:
                return "0"

        # Tous ces chiffres sont calcules UNIQUEMENT sur les factures enregistrees
        tot_ht_d = round(sum(_val(v) or 0 for v in df_dashboard["Montant HT"]), 2)
        tot_tva_d = round(sum(_val(v) or 0 for v in df_dashboard["Montant TVA"]), 2)
        tot_ttc_d = round(sum(_val(v) or 0 for v in df_dashboard["Montant TTC"]), 2)
        tot_paye_d = round(sum(_val(r.get("Montant payé")) or 0
                               for r in st.session_state["recap"]), 2)
        reste_affiche = 0.0
        for r in st.session_state["recap"]:
            if str(r.get("Payée ?", "")).upper() != "OUI":
                reste_affiche += (_reste_a_payer(r) or 0)
        reste_affiche = round(reste_affiche, 2)
        taux_rec = (100.0 * tot_paye_d / tot_ttc_d) if tot_ttc_d else 0.0
        ajd_d = datetime.date.today()
        retards_d = []
        for r in st.session_state["recap"]:
            if str(r.get("Payée ?", "")).upper() == "OUI":
                continue
            ech_d = _date_depuis_txt(r.get("Date échéance"))
            if ech_d and ech_d < ajd_d:
                retards_d.append(r)

        def _carte(libelle, valeur, classe=""):
            return ('<div class="carte-kpi ' + classe + '"><div class="kpi-lib">' +
                    libelle + '</div><div class="kpi-val">' + valeur + "</div></div>")

        k1, k2, k3, k4 = st.columns(4)
        k1.markdown(_carte("Factures enregistrées", str(len(df_dashboard))), unsafe_allow_html=True)
        k2.markdown(_carte("Total HT", _fm(tot_ht_d)), unsafe_allow_html=True)
        k3.markdown(_carte("Total TVA", _fm(tot_tva_d), "bleu"), unsafe_allow_html=True)
        k4.markdown(_carte("Total TTC", _fm(tot_ttc_d)), unsafe_allow_html=True)
        k5, k6, k7, k8 = st.columns(4)
        k5.markdown(_carte("💰 Déjà payé", _fm(tot_paye_d), "vert"), unsafe_allow_html=True)
        k6.markdown(_carte("💸 Reste à encaisser", _fm(reste_affiche), "orange"), unsafe_allow_html=True)
        k7.markdown(_carte("📊 Recouvrement", str(round(taux_rec, 1)).replace(".", ",") + " %"), unsafe_allow_html=True)
        k8.markdown(_carte("🚨 En retard", str(len(retards_d)), "rouge" if retards_d else ""), unsafe_allow_html=True)

        if retards_d:
            with st.expander("🚨 Factures en retard (" + str(len(retards_d)) + ")",
                             expanded=True):
                for r in retards_d:
                    st.error("🚨 **" + str(r.get("Fournisseur", "?")) + "** n° " +
                             str(r.get("N° facture", "?")) + " — échéance dépassée depuis le " +
                             str(r.get("Date échéance")) + " · reste " + str(_reste_a_payer(r)))
        elif reste_affiche > 0.005:
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
        if r.get("bloquees"):
            st.error("🚫 **" + str(r["bloquees"]) + " facture(s) non ajoutée(s) : limite de " +
                     str(LIMITE_FACTURES) + " factures atteinte.** Supprime d'anciennes " +
                     "factures (ou exporte-les) pour libérer de la place — rien n'est " +
                     "effacé automatiquement.")
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
        with st.spinner("🧠 Analyse IA de " + f.name + "..."):
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
            ajoutees, passees, bloquees = 0, [], 0
            recap_actuel = st.session_state["recap"]
            for r in valides:
                ligne = r["donnees"]
                doublon, _ = est_doublon(ligne, recap_actuel)
                if doublon:
                    passees.append(r["f"].name + " (doublon)")
                    continue
                if limite_atteinte(recap_actuel):   # v28 : 500 max
                    bloquees += 1
                    continue
                db_ajouter(ligne)
                sauver_pdf(ligne, r["f"])   # v17 : garde le PDF pour le comptable
                recap_actuel = recap_actuel + [ligne]
                ajoutees += 1
            st.session_state["recap"] = db_charger()
            st.session_state["rapport_lot"] = {"ajoutees": ajoutees, "passees": passees,
                                              "bloquees": bloquees}
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
            # Notes d'analyse du moteur
            if r["notes"]:
                for note in r["notes"]:
                    st.info("🧠 **Analyse IA** : " + note)

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

            # v29 : rappel de fiabilite OCR — coherence mathematique ne
            # garantit pas l'exactitude de la lecture.
            st.caption("⚠️ Vérifie les champs avant d'enregistrer : un trio cohérent "
                       "(HT + TVA = TTC) ne prouve pas que les montants lus sont les bons "
                       "— un scan flou peut produire des chiffres faux mais cohérents.")
            if st.button("➕ Ajouter au récapitulatif", key="btn_" + f.name, type="primary",
                         disabled=bool(manquants or alertes) or limite_atteinte()):
                         disabled=bool(manquants or alertes) or limite_atteinte()):
                ligne = {c: df_corrige.iloc[0][c] for c in CHAMPS}
                if not db_ajouter(ligne):
                    st.error("🚫 **Limite de " + str(LIMITE_FACTURES) + " factures atteinte.** "
                             "Exporte puis supprime d'anciennes factures pour libérer de la place.")
                    st.stop()
                sauver_pdf(ligne, f)   # v17 : garde le PDF pour le comptable
                st.session_state["recap"] = db_charger()
                st.rerun()

            # Diagnostic : texte lu par l'application
            with st.expander("🔍 Diagnostic — texte lu par l'application"):
                st.caption("Copie-colle ce texte dans notre conversation si un champ est faux : "
                           "je corrige la règle de lecture.")
                st.code(r.get("texte", "")[:4000], language="text")

elif page == "📄 Factures PDF":
    st.info("👆 Dépose une facture PDF — ou ouvre « 🧪 Factures d'exemple » dans la barre "
            "latérale pour essayer l'application avec les 3 PDF fournis, en 30 secondes.")

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
                            # le bouton de confirmation DOIT survivre au rechargement
                            # de la page : on stocke la facture a confirmer.
                            st.session_state["solde_a_confirmer"] = {
                                "Fournisseur": cible.get("Fournisseur", ""),
                                "N° facture": cible.get("N° facture", "")}
                            st.rerun()
                        else:
                            st.success("Paiement enregistré ✔ — reste à payer : " +
                                       str(reste_apres) + " (statut : Partiellement payée)")

        # --- Confirmation humaine apres un paiement soldant (v26 : le
        # bouton survit au rechargement, il est donc reellement cliquable) ---
        if st.session_state.get("solde_a_confirmer"):
            info_c = st.session_state["solde_a_confirmer"]
            st.warning("⚠️ La facture **" + str(info_c.get("Fournisseur", "?")) + "** n° " +
                       str(info_c.get("N° facture", "?")) + " est soldée mais **pas encore** "
                       "marquée « Payée » : c'est TOI qui confirmes.")
            cc1, cc2 = st.columns(2)
            if cc1.button("✔️ CONFIRMER : cette facture est entièrement payée",
                          key="btn_confirm_payee", type="primary", use_container_width=True):
                cible_c = [r for r in st.session_state["recap"]
                           if str(r.get("N° facture", "")).upper() ==
                           str(info_c.get("N° facture", "")).upper()
                           and str(r.get("Fournisseur", "")).lower() ==
                           str(info_c.get("Fournisseur", "")).lower()]
                if cible_c:
                    maj2 = dict(cible_c[0])
                    maj2["Payée ?"] = "OUI"
                    db_remplacer_numero(cible_c[0], maj2)
                    st.session_state["recap"] = db_charger()
                    st.session_state.pop("solde_a_confirmer", None)
                    st.success("Facture confirmée payée ✔")
                    st.rerun()
                else:
                    st.error("Facture introuvable : elle a peut-être été modifiée.")
            if cc2.button("❌ Pas maintenant", key="btn_confirm_plus_tard",
                          use_container_width=True):
                st.session_state.pop("solde_a_confirmer", None)
                st.rerun()

        # --- Historique des paiements ---
        st.markdown("### 📜 Historique des paiements")
        avec_hist = [l for l in lignes_p if l.get("Historique paiements")]
        if not avec_hist:
            st.caption("Aucun paiement enregistré pour l'instant.")
        # v26.1 : les cles des boutons utilisent l'INDEX de la ligne, jamais
        # le numero de facture : deux factures avec le meme numero (ou sans
        # numero) ne font plus planter l'application (StreamlitDuplicateElementKey).
        for idx_h, l in enumerate(avec_hist):
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
                                 key="confirm_" + str(idx_h)):
                        maj = dict(l)
                        maj["Payée ?"] = "OUI"
                        db_remplacer_numero(l, maj)
                        st.session_state["recap"] = db_charger()
                        st.rerun()
                # v18 : annuler le dernier paiement (erreur de saisie, chèque refusé...)
                if l.get("Historique paiements"):
                    if st.button("↩️ Annuler le dernier paiement",
                                 key="annuler_" + str(idx_h)):
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

# ---------- PAGE : ASSISTANT IA ----------

if page == "🤖 Assistant":
    st.markdown("### 🤖 Assistant Factures IA")

    # v28 : le fil de conversation est recharge depuis la sauvegarde en ligne
    # (table protegee par RLS) a la premiere ouverture de la page.
    if "ia_messages" not in st.session_state:
        st.session_state["ia_messages"] = cloud_conversation_charger()

    if _cle_mistral():
        st.caption("🧠 Assistant IA complet (Mistral), **avec mémoire de la "
                   "conversation** : tu peux poser des questions de suivi. "
                   "⚠️ Pour répondre, tes factures enregistrées et tes "
                   "10 derniers échanges sont envoyés à l'API Mistral.")
        mode_a = "IA"
    else:
        st.caption("Assistant local : réponds uniquement avec tes factures enregistrées, "
                   "jamais d'invention, **rien n'est envoyé sur Internet**. 💡 Pour un "
                   "assistant complet avec mémoire, ajoute une clé Mistral dans les "
                   "Secrets — voir la page « ❓ Aide ».")
        mode_a = "local"

    # --- Contrôles de l'historique (l'utilisateur décide) ---
    c_ia1, c_ia2 = st.columns(2)
    if c_ia1.button("🧹 Nouvelle conversation", key="btn_nouvelle_conv",
                    use_container_width=True, disabled=not st.session_state["ia_messages"]):
        st.session_state["ia_messages"] = []
        cloud_conversation_supprimer()
        st.rerun()
    if c_ia2.button("🗑️ Effacer l'historique en ligne", key="btn_suppr_conv",
                    use_container_width=True,
                    disabled=not st.session_state["ia_messages"],
                    help="Supprime la conversation enregistrée dans Supabase "
                         "(uniquement la tienne)."):
        st.session_state["ia_messages"] = []
        cloud_conversation_supprimer()
        st.success("✅ Historique effacé.")
        st.rerun()
    if st.session_state["ia_messages"]:
        st.caption("💾 " + str(len(st.session_state["ia_messages"])) +
                   " message(s) dans cette conversation — elle est conservée, "
                   "tu peux la reprendre plus tard. Seuls les " +
                   str(MAX_MESSAGES_CONTEXTE) + " derniers échanges servent de "
                   "contexte à l'IA.")

    # --- Affichage du fil ---
    for m in st.session_state["ia_messages"]:
        if m.get("role") == "user":
            with st.chat_message("user"):
                st.markdown(m["content"])
        else:
            with st.chat_message("assistant"):
                st.markdown(m["content"])

    # --- Suggestions (uniquement si conversation neuuve) ---
    if not st.session_state["ia_messages"]:
        suggestions_a = ["Combien me reste-t-il à payer ?",
                         "Quelles factures sont en retard ?",
                         "Quelles sont les prochaines échéances ?",
                         "Combien de TVA au total ?",
                         "Par quels moyens ai-je payé ?",
                         "Total de mes factures ?"]
        sug_cols = st.columns(3)
        for i_a, sugg in enumerate(suggestions_a):
            if sug_cols[i_a % 3].button(sugg, key="sugg_" + str(i_a),
                                        use_container_width=True):
                st.session_state["prochaine_question_ia"] = sugg
                st.rerun()

    # v28.1 : plus de parametre 'default' (inexistant sur certaines versions
    # de Streamlit -> TypeError). Une question-exemple cliquee est traitee
    # directement comme une question envoyee.
    question_a = st.chat_input("Ta question (ex : combien dois-je à ENTREPRISE DUPONT ?)")
    if not question_a:
        question_a = st.session_state.pop("prochaine_question_ia", "") or None

    if question_a and question_a.strip():
        st.session_state["ia_messages"].append(
            {"role": "user", "content": question_a.strip()})
        reponse_a = None
        if mode_a == "IA":
            with st.spinner("🧠 L'IA réfléchit..."):
                reponse_a, err_ia = repondre_question_ia(
                    question_a, st.session_state["recap"],
                    st.session_state["ia_messages"])
            if reponse_a is None:
                st.caption("⚠️ IA indisponible (" + str(err_ia) +
                           ") — réponse calculée par le moteur local à la place.")
        if reponse_a is None:
            reponse_a = repondre_question(question_a, st.session_state["recap"])
            reponse_a += "\n\n*💡 Réponse du moteur local : pour un vrai dialogue "
            reponse_a += "avec mémoire, active l'assistant IA complet (page « ❓ Aide »).*"
        st.session_state["ia_messages"].append(
            {"role": "assistant", "content": reponse_a})
        # plafonne l'historique conservé (les vieux échanges sont éliminés)
        st.session_state["ia_messages"] = st.session_state["ia_messages"][-MAX_MESSAGES_STOCKES:]
        cloud_conversation_sauver(st.session_state["ia_messages"])
        st.rerun()   # réaffiche le fil complet avec la nouvelle réponse
    elif not st.session_state["ia_messages"]:
        st.info("👆 Pose ta première question (ou choisis un exemple plus haut).")

# ---------- PAGE : AIDE ----------

if page == "❓ Aide":
    st.markdown("### ❓ Aide & explications")

    with st.expander("🧠 C'est quoi le moteur d'analyse IA ?"):
        st.markdown("""
        Un moteur d'analyse **profonde** :

        - 🧮 Il collecte **tous** les montants candidats de la facture (HT, TTC, TVA, tables de TVA).
        - ✔ Il teste **toutes les combinaisons** et ne garde que celles où **HT + TVA = TTC**.
        - 🔢 Il lit les **tables de TVA** (« Taux | Base | Montant ») et fait la somme.
        - 💡 Il peut **déduire la TVA** (TTC − HT) quand elle est invisible, si le taux confirme.
        - 📊 Il affiche un **score de confiance** et ses notes d'analyse.

        **Règle d'or inchangée : jamais inventer.** Si rien n'est sûr, le champ reste à vérifier.""")

    with st.expander("❓ C'est quoi HT, TVA, TTC ?"):
        st.markdown("""
        - **HT** = *Hors Taxes* : le prix sans la taxe.
        - **TVA** : le % facturé en plus et reversé à l'État.
        - **TTC** = *Toutes Taxes Comprises* : le total à payer.

        🧮 **Règle d'or : HT + TVA = TTC.**""")

    with st.expander("🤖 L'assistant et sa mémoire : comment ça marche ?"):
        st.markdown("""
        **Deux modes, ton choix** :

        - **Sans clé API** : l'assistant **local** répond uniquement en calculant
        sur tes factures enregistrées. **Rien ne quitte ta machine.**
        - **Avec clé Mistral** (Secrets Streamlit) : un vrai assistant conversationnel.
        Pour te répondre, il reçoit **le résumé de tes factures + tes 10 derniers
        échanges**. C'est envoyé à l'API Mistral (payante à l'usage, très peu
        chère à ton volume) — et c'est affiché sur la page Assistant.

        **La mémoire** :

        - Le fil de la conversation est **conservé** (table `conversations`
        protégée par RLS : chaque compte ne voit que sa conversation).
        - Tu peux **reprendre** une discussion plus tard, l'**effacer** (bouton
        « 🗑️ Effacer l'historique en ligne »), ou repartir de zéro
        (« 🧹 Nouvelle conversation »).
        - Seuls les **10 derniers échanges** servent de contexte à l'IA ; au-delà
        de 30 messages, les plus anciens sont éliminés. Aucune donnée de facture
        n'est conservée dans la conversation plus longtemps que nécessaire.
        - **Aucun PDF** n'est jamais envoyé à l'IA : uniquement les résumés
        (fournisseur, n°, dates, montants, paiements).""")

    with st.expander("🗂️ La limite de 500 factures"):
        st.markdown("""
        - Chaque compte peut enregistrer jusqu'à **500 factures**. Une facture =
        **une ligne validée du récapitulatif** (un PDF de 3 pages compte pour
        1 facture ; un fichier renommé 10 fois compte aussi pour 1).
        - Le compteur dans le menu de gauche affiche toujours ta capacité
        restante. À la limite atteinte : un message clair s'affiche et l'ajout
        est refusé.
        - La limite est **aussi appliquée côté serveur** (règle Supabase) : la
        modifier dans l'application ne suffit pas à la contourner.
        - **Aucune facture n'est jamais effacée automatiquement** pour libérer
        de la place : c'est toi qui exportes puis supprimes.""")

    st.caption("💡 Pour l'installation de l'OCR (Tesseract), les comptes et la "
               "sauvegarde Supabase : regarde les expanders de la colonne de gauche.")

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
                conf_suppr = st.checkbox("Je confirme vouloir supprimer CETTE facture",
                                         key="conf_suppr")
                if st.button("Supprimer cette facture", key="btn_suppr"):
                    if not conf_suppr:
                        st.warning("Coche d'abord la case de confirmation.")
                    elif num_suppr.strip():
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
                st.divider()
                conf_vider = st.checkbox("⚠️ Je comprends que TOUTES les factures et paiements "
                                         "seront effacés (local + en ligne)",
                                         key="conf_vider")
                if st.button("Tout vider", key="btn_vider"):
                    if conf_vider:
                        db_vider()
                        st.session_state["recap"] = []
                        st.rerun()
                    else:
                        st.warning("Coche d'abord la case de confirmation. Action annulée, "
                                   "aucune donnée n'a été touchée.")

st.caption("Factures IA — version 29 · " + datetime.date.today().strftime("%d/%m/%Y"))
