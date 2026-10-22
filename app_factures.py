# -*- coding: utf-8 -*-

"""FACTURES IA v6 - TOUT-EN-UN : extraction fiable, sauvegarde SQLite, doublons, export."""



import re

import io

import os

import json

import unicodedata

import datetime

import sqlite3



import streamlit as st

import pymupdf  # PyMuPDF

import pandas as pd



# ============================================================

# 1. OUTILS DE BASE

# ============================================================



@st.cache_data(show_spinner=False)

def lire_texte_pdf(fichier):

    try:

        doc = pymupdf.open(stream=fichier.getvalue(), filetype="pdf")

        texte = "\n".join(page.get_text() for page in doc)

        doc.close()

        return texte.strip()

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

# 3. EXTRACTION (par lignes = fiable)

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

    j, mois, a = m.group(1), m.group(2), m.group(3)

    if len(a) == 2:

        a = "20" + a

    return "%02d/%02d/%04d" % (int(j), int(mois), int(a))





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

    lignes = t.splitlines()

    for i, ligne in enumerate(lignes):

        ln = norme(ligne)

        if any(re.search(e, ln) for e in etiquettes):

            v = _montant_sur_ligne(ligne)

            if v is not None:

                return v

            if i + 1 < len(lignes):

                v = _montant_sur_ligne(lignes[i + 1])

                if v is not None:

                    return v

    return None





def extraire_numero(t):
    """Extrait le numéro de facture sans utiliser de classe regex mal formée."""
    motifs = [
        r"(?:facture|invoice)\s*(?:n[°º.]?|no|number|#)?\s*[:#.]?\s*([A-Za-z0-9][A-Za-z0-9./_-]{1,})",
        r"no\s*de\s*facture\s*[:#.]?\s*([A-Za-z0-9][A-Za-z0-9./_-]{1,})",
    ]

    for ligne in t.splitlines():
        ln = norme(ligne)
        if "date" in ln:
            continue

        for motif in motifs:
            m = re.search(motif, ln)
            if m:
                candidat = m.group(1).strip(" :.-#")
                if candidat and candidat.lower() not in ("de", "du", "client"):
                    return candidat.upper()

    return None




def extraire_date_facture(t):

    lignes = t.splitlines()

    etiquettes = (r"date\s*(?:de\s*)?(?:facture|emission|d.emission)", r"invoice\s*date",

                  r"emise\s*le", r"date\s*facture")

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

    return None





def extraire_date_echeance(t):

    lignes = t.splitlines()

    etiquettes = (r"echeance", r"due\s*date", r"payment\s*due",

                  r"a\s*payer\s*(?:avant|le)", r"date\s*limite")

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

    return None





def extraire_fournisseur(texte):

    lignes = [l.strip() for l in texte.splitlines() if l.strip()]

    parasites = (r"facture|invoice|total|siret|tva|vat|date|adresse|client|page|tel|www|http|email|@",

                 r"rue|avenue|cite|street|harbor|bp |cedex|sarl|sas|sa\b|eurl|\d{4,}")

    for l in lignes[:12]:

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





def extraire_tva(t):

    taux = None

    m = re.search(r"(?:tva|vat|taxe)[^0-9%]{0,25}(\d{1,2}[.,]?\d{0,2})\s*%", t)

    if m:

        try:

            taux = float(m.group(1).replace(",", "."))

        except ValueError:

            pass

    montant = extraire_montant(t, [r"montant\s*(?:de\s*)?tva", r"\btva\b", r"\bvat\b"])

    return taux, montant



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

    """Meme fournisseur + meme numero OU meme fournisseur + meme TTC + meme date."""

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

# 5. SAUVEGARDE SQLite (les donnees survivent a la fermeture)

# ============================================================



DOSSIER = os.path.dirname(os.path.abspath(__file__))

BDD = os.path.join(DOSSIER, "factures.db")





def db_connexion():

    return sqlite3.connect(BDD)





def db_creer():

    with db_connexion() as c:

        c.execute("""CREATE TABLE IF NOT EXISTS factures (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            donnees TEXT NOT NULL,

            date_ajout TEXT NOT NULL)""")





def db_charger():

    db_creer()

    with db_connexion() as c:

        lignes = c.execute("SELECT donnees FROM factures ORDER BY id").fetchall()

    return [json.loads(l[0]) for l in lignes]





def db_ajouter(ligne):

    db_creer()

    with db_connexion() as c:

        c.execute("INSERT INTO factures (donnees, date_ajout) VALUES (?, ?)",

                  (json.dumps(ligne, ensure_ascii=False), datetime.date.today().isoformat()))





def db_remplacer_numero(ancien, nouveau):

    """Met a jour une ligne identifiee par son numero + fournisseur."""

    db_creer()

    with db_connexion() as c:

        for (i, donnees_txt) in c.execute("SELECT id, donnees FROM factures").fetchall():

            d = json.loads(donnees_txt)

            if str(d.get("N° facture", "")).upper() == str(ancien.get("N° facture", "")).upper() \
                    and str(d.get("Fournisseur", "")).lower() == str(ancien.get("Fournisseur", "")).lower():

                c.execute("UPDATE factures SET donnees = ? WHERE id = ?",

                          (json.dumps(nouveau, ensure_ascii=False), i))





def db_vider():

    with db_connexion() as c:

        c.execute("DELETE FROM factures")





def db_supprimer(numero, fournisseur):

    db_creer()

    with db_connexion() as c:

        for (i, donnees_txt) in c.execute("SELECT id, donnees FROM factures").fetchall():

            d = json.loads(donnees_txt)

            if str(d.get("N° facture", "")).upper() == str(numero).upper() \
                    and str(d.get("Fournisseur", "")).lower() == str(fournisseur).lower():

                c.execute("DELETE FROM factures WHERE id = ?", (i,))



# ============================================================

# 6. DESIGN

# ============================================================



st.set_page_config(page_title="Factures IA", page_icon="🧾", layout="wide")



st.markdown("""

<style>

@import url('https\://fonts.googleapis.com/css2?family=Inter:wght\@400;500;600;700;800&display=swap');

    .block-container {padding-top: 1.4rem; max-width: 1150px; font-family: 'Inter', sans-serif;}

    .hero {

        background: linear-gradient(115deg, #312e81 0%, #4f46e5 55%, #6366f1 100%);

        color: #fff; padding: 1.8rem 2.2rem; border-radius: 18px; margin-bottom: 1rem;

        box-shadow: 0 10px 30px rgba(79,70,229,.25);

    }

    .hero h1 {color: #fff; margin: 0; font-size: 1.9rem; font-weight: 800; letter-spacing: -.5px;}

    .hero p {color: #e0e7ff; margin: .45rem 0 0 0; font-size: 1rem;}

    .etapes span {

        display: inline-block; background: #eef2ff; color: #4338ca; border: 1px solid #e0e7ff;

        border-radius: 20px; padding: .35rem .95rem; margin: .25rem .28rem 0 0;

        font-weight: 600; font-size: .9rem;

    }

    div[data-testid="stExpander"] {

        border-radius: 14px; border: 1px solid #e2e8f0; background: #fff;

        box-shadow: 0 2px 8px rgba(49,46,129,.06); overflow: hidden;

    }







section[data-testid="stSidebar"] {

    background: #25282E !important;

    border-right: 1px solid #3A3F47;

}



section[data-testid="stSidebar"] * {

    color: #E0E3E8 !important;

}





    .stTabs [data-baseweb="tab-highlight"] {background: #4f46e5;}

    .stTabs [data-baseweb="tab"] {font-weight: 600;}

    div[data-testid="stMetric"] {

        background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: .9rem;

        box-shadow: 0 2px 8px rgba(49,46,129,.05);

    }

    .stButton > button[kind="primary"] {

        background: #4f46e5; border-color: #4f46e5; border-radius: 10px; font-weight: 600;

    }

    .stButton > button[kind="primary"]:hover {background: #4338ca; border-color: #4338ca;}

    .stDownloadButton > button {border-radius: 10px; font-weight: 600;}

    div[data-testid="stFileUploader"] {

        border: 1.5px dashed #c7d2fe; border-radius: 14px; padding: .7rem; background: #f8faff;

    }

</style>

""", unsafe_allow_html=True)



st.markdown("""

<div class="hero">

  <h1>🧾 Factures IA</h1>

  <p>Lit tes factures PDF, vérifie les montants, détecte les doublons et prépare ta saisie comptable en quelques clics.</p>

</div>

<div class="etapes">

  <span>1️⃣ Dépose tes PDF</span>

  <span>2️⃣ Vérifie les infos</span>

  <span>3️⃣ Corrige si besoin</span>

  <span>4️⃣ Ajoute au récapitulatif</span>

  <span>5️⃣ Exporte en CSV / Excel</span>

</div>

""", unsafe_allow_html=True)



# ============================================================

# 7. SIDEBAR

# ============================================================



with st.sidebar:

    st.markdown("### 🧾 Factures IA")

    st.caption("Version 6 · 100 % local · sauvegarde automatique")

    st.divider()

    st.markdown("### 🧭 Navigation")
    page = st.radio(
        "Choisir une page",
        ["🏠 Tableau de bord", "📄 Factures PDF", "📋 Récapitulatif"],
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



    with st.expander("❓ C'est quoi HT, TVA, TTC ?"):

        st.markdown("""

        - **HT** = *Hors Taxes* : le prix sans la taxe.

        - **TVA** : le % facturé en plus et reversé à l'État.

        - **TTC** = *Toutes Taxes Comprises* : le total à payer.



        🧮 **Règle d'or : HT + TVA = TTC.**

        """)

    with st.expander("📄 Quelles factures sont lisibles ?"):

        st.markdown("- ✅ **PDF numériques** (texte sélectionnable) : parfait.\n"

                    "- ⚠️ **Scans / photos** : illisibles pour l'instant — l'OCR (Tesseract) arrive plus tard.")

    with st.expander("🛡️ Où vont mes données ?"):

        st.markdown("Nulle part. Tout est traité **sur ton ordinateur** : "

                    "les factures validées sont sauvegardées dans le fichier \`factures.db\` du dossier de l'application, "

                    "aucune donnée ne quitte ton PC.")



# ============================================================

# 8. TRAITEMENT

# ============================================================



if "recap" not in st.session_state:

    st.session_state["recap"] = db_charger()



CHAMPS = ["Fournisseur", "N° facture", "Date facture", "Date échéance",

          "Montant HT", "Taux TVA (%)", "Montant TVA", "Montant TTC", "Devise"]

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

}





def _val(x):

    try:

        return float(str(x).replace(",", ".")) if str(x).strip() else None

    except ValueError:

        return None





fichiers = None
if page == "📄 Factures PDF":
    fichiers = st.file_uploader(
        "📂 Dépose tes factures PDF",
        type=["pdf"], accept_multiple_files=True,
        help="Plusieurs PDF à la fois. Les PDF « numériques » fonctionnent le mieux.",
    )


if page == "🏠 Tableau de bord":
    st.title("🏠 Tableau de bord")
    factures_dashboard = st.session_state["recap"]
    if not factures_dashboard:
        st.info("Aucune facture enregistrée pour le moment. Ouvre « Factures PDF » pour commencer.")
    else:
        df_dashboard = pd.DataFrame(factures_dashboard, columns=CHAMPS)
        st.markdown("Voici le résumé de tes factures enregistrées.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Factures enregistrées", len(df_dashboard))
        c2.metric("Montant total TTC", f"{sum(_val(x) or 0 for x in df_dashboard['Montant TTC']):,.2f} €")
        c3.metric("Fournisseurs", df_dashboard["Fournisseur"].fillna("").nunique())
        st.dataframe(df_dashboard, use_container_width=True, hide_index=True)


if page == "📄 Factures PDF":

    if not fichiers:

        st.info("👆 Dépose une facture PDF — ou ouvre « 🗂️ Factures de test » à gauche pour essayer avec les 3 PDF d'exemple.")



    for f in fichiers:

        texte = lire_texte_pdf(f)

        if texte is None:

            st.error("Impossible d'ouvrir : " + f.name)

            continue

        if len(texte) < 40:

            with st.expander("📄 " + f.name + " — scan détecté", expanded=False):

                st.error("Ce PDF ne contient pas de texte lisible (probablement un scan ou une photo). "

                         "L'étape OCR pour lire ces factures arrive dans une prochaine version.")

            continue



        t = norme(texte)

        ht = extraire_montant(t, [r"total\s*hors\s*taxes", r"total\s*ht", r"montant\s*ht",

                                  r"subtotal", r"sous[- ]total", r"\bht\b"])

        ttc = extraire_montant(t, [r"net\s*a\s*payer", r"total\s*(?:à|a)\s*payer", r"amount\s*due",

                                   r"balance\s*due", r"total\s*ttc", r"montant\s*ttc",

                                   r"grand\s*total", r"\bttc\b", r"\btotal\b"])

        taux, montant_tva = extraire_tva(t)



        donnees = {

            "Fournisseur": extraire_fournisseur(texte),

            "N° facture": extraire_numero(t),

            "Date facture": extraire_date_facture(t),

            "Date échéance": extraire_date_echeance(t),

            "Montant HT": ht,

            "Taux TVA (%)": taux,

            "Montant TVA": montant_tva,

            "Montant TTC": ttc,

            "Devise": extraire_devise(t, texte),

        }



        trouves = sum(1 for v in donnees.values() if v not in (None, ""))

        if trouves >= 7:

            badge = "🟢 **" + str(trouves) + "/9 champs lus**"

        elif trouves >= 4:

            badge = "🟠 **" + str(trouves) + "/9 champs lus**"

        else:

            badge = "🔴 **" + str(trouves) + "/9 champs lus**"



        with st.expander("📄 " + f.name + "   " + badge, expanded=(len(fichiers) == 1)):

            df = pd.DataFrame([donnees], columns=CHAMPS).fillna("")

            # Editeur simple et compatible : évite les erreurs de configuration
            # de colonnes lorsque les montants extraits sont numériques.
            df_corrige = st.data_editor(
                df,
                num_rows="fixed",
                key="ed_" + f.name,
                use_container_width=True,
            )



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



            # Detection de doublon AVANT l'ajout

            ligne_candidate = {c: df_corrige.iloc[0][c] for c in CHAMPS}

            doublon, existante = est_doublon(ligne_candidate, st.session_state["recap"])

            if doublon:

                st.error("🔁 **Doublon détecté** : une facture de « " + str(existante.get("Fournisseur")) +

                         " » n° " + str(existante.get("N° facture")) +

                         " est déjà enregistrée. Vérifie que ce n'est pas la même.")

                if st.button("♻️ C'est une correction : remplacer l'existante",

                             key="maj_" + f.name):

                    db_remplacer_numero(existante, ligne_candidate)

                    st.session_state["recap"] = db_charger()

                    st.success("Facture mise à jour.")

                    st.rerun()



            if st.button("➕ Ajouter au récapitulatif", key="btn_" + f.name, type="primary",

                         disabled=bool(manquants or alertes)):

                ligne = {c: df_corrige.iloc[0][c] for c in CHAMPS}

                db_ajouter(ligne)

                st.session_state["recap"] = db_charger()

                st.rerun()



if page == "📋 Récapitulatif":

    if not st.session_state["recap"]:

        st.info("Rien pour l'instant. Valide des factures dans l'onglet « 📄 Mes factures », elles arriveront ici "

                "— et **elles restent enregistrées même si tu fermes l'application**.")

    else:

        df_recap = pd.DataFrame(st.session_state["recap"], columns=CHAMPS)

        st.markdown("### 📋 " + str(len(st.session_state["recap"])) + " facture(s) enregistrée(s)")

        m1, m2, m3 = st.columns(3)

        tot_ht = sum(_val(v) or 0 for v in df_recap["Montant HT"])

        tot_tva = sum(_val(v) or 0 for v in df_recap["Montant TVA"])

        tot_ttc = sum(_val(v) or 0 for v in df_recap["Montant TTC"])

        m1.metric("Total HT", str(round(tot_ht, 2)))

        m2.metric("Total TVA", str(round(tot_tva, 2)))

        m3.metric("Total TTC", str(round(tot_ttc, 2)))

        st.dataframe(df_recap, use_container_width=True, hide_index=True)

        st.caption("💡 Besoin d'enlever une ligne ? Utilise la suppression ci-dessous en donnant son numéro de facture.")



        c1, c2, c3 = st.columns(3)

        st.download_button("⬇️ Exporter en CSV", df_recap.to_csv(index=False).encode("utf-8-sig"),

                           file_name="recapitulatif_factures.csv", mime="text/csv",

                           key="dl_csv", use_container_width=True)

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



st.caption("Factures IA — version 6 · " + datetime.date.today().strftime("%d/%m/%Y"))
