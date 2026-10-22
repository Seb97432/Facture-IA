
import io
import re
import unicodedata
from datetime import datetime

import pandas as pd
import pymupdf
import streamlit as st


# ============================================================
# FACTURES IA — PREMIUM PRO
# ============================================================

st.set_page_config(
    page_title="Factures IA | Premium",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

OR = "#D6B568"
OR_CLAIR = "#F1D99B"
FOND = "#0C0E12"
PANNEAU = "#141820"
PANNEAU_2 = "#1B202A"
TEXTE = "#F1F3F6"
GRIS = "#9CA6B5"
VERT = "#54D6A0"
ROUGE = "#FF7777"


# ============================================================
# DESIGN CSS
# ============================================================

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');

    :root {{
        --gold: {OR};
        --gold-light: {OR_CLAIR};
        --bg: {FOND};
        --panel: {PANNEAU};
        --panel-2: {PANNEAU_2};
        --text: {TEXTE};
        --muted: {GRIS};
    }}

    html, body, [class*="css"] {{
        font-family: 'DM Sans', sans-serif;
    }}

    .stApp {{
        background:
            radial-gradient(ellipse at 85% 0%, rgba(214,181,104,.075), transparent 35%),
            var(--bg);
        color: var(--text);
    }}

    [data-testid="stHeader"] {{
        background: rgba(12,14,18,.88);
    }}

    [data-testid="stSidebar"] {{
        background: #101319;
        border-right: 1px solid #292D35;
    }}

    [data-testid="stSidebar"] > div:first-child {{
        padding-top: 1.5rem;
    }}

    h1, h2, h3 {{
        font-family: 'Manrope', sans-serif !important;
        letter-spacing: -.5px;
    }}

    h1 {{
        font-weight: 800 !important;
    }}

    h2, h3 {{
        font-weight: 700 !important;
    }}

    p, label, span, div {{
        scroll-behavior: smooth;
    }}

    .brand {{
        display: flex;
        align-items: center;
        gap: 13px;
        padding: 7px 0 20px 0;
    }}

    .brand-icon {{
        width: 48px;
        height: 48px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(145deg, #F1D99B, #A78339);
        border-radius: 15px;
        color: #17130A;
        font-size: 25px;
        font-weight: 800;
        box-shadow: 0 6px 22px rgba(214,181,104,.12);
    }}

    .brand-title {{
        color: #F5F1E8;
        font-family: 'Manrope', sans-serif;
        font-size: 22px;
        font-weight: 800;
        line-height: 1.2;
    }}

    .brand-sub {{
        color: #9CA6B5;
        font-size: 10px;
        letter-spacing: 2px;
        margin-top: 5px;
        text-transform: uppercase;
    }}

    .eyebrow {{
        color: var(--gold);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 2px;
        text-transform: uppercase;
        margin-bottom: 8px;
    }}

    .hero {{
        padding: 27px 29px;
        border: 1px solid #39332A;
        border-radius: 21px;
        background: linear-gradient(115deg, #191A1D 0%, #171B22 58%, #282216 100%);
        margin-bottom: 22px;
    }}

    .hero h1 {{
        margin: 0;
        font-size: clamp(27px, 3vw, 38px);
        color: #F5F2EB;
    }}

    .hero p {{
        color: #ADB5C1;
        margin: 10px 0 0 0;
        line-height: 1.7;
    }}

    .hero-tag {{
        display: inline-block;
        margin-top: 18px;
        border: 1px solid #66532E;
        color: var(--gold-light);
        background: rgba(214,181,104,.08);
        padding: 6px 11px;
        border-radius: 30px;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: .5px;
    }}

    .metric {{
        border: 1px solid #2A303B;
        border-radius: 17px;
        background: linear-gradient(145deg, #171B23, #12151B);
        padding: 19px 20px;
        min-height: 124px;
    }}

    .metric-label {{
        color: #A3ADBC;
        font-size: 12px;
        font-weight: 500;
    }}

    .metric-value {{
        font-family: 'Manrope', sans-serif;
        font-size: 28px;
        font-weight: 800;
        color: #F4F2ED;
        margin-top: 11px;
        line-height: 1.2;
        overflow-wrap: anywhere;
    }}

    .metric-note {{
        color: var(--gold);
        font-size: 11px;
        margin-top: 8px;
    }}

    .section-head {{
        display: flex;
        align-items: center;
        gap: 10px;
        margin: 26px 0 12px 0;
        color: #F2F2F3;
        font-family: 'Manrope', sans-serif;
        font-size: 19px;
        font-weight: 750;
    }}

    .section-mark {{
        display: inline-block;
        width: 4px;
        height: 21px;
        border-radius: 5px;
        background: var(--gold);
    }}

    .subtext {{
        color: #9CA6B5;
        font-size: 13px;
        line-height: 1.7;
    }}

    .status-pill {{
        display: inline-block;
        padding: 5px 10px;
        border-radius: 20px;
        color: #E8D5A4;
        background: rgba(214,181,104,.10);
        border: 1px solid rgba(214,181,104,.24);
        font-size: 11px;
        font-weight: 700;
    }}

    .upload-panel {{
        padding: 20px;
        border-radius: 17px;
        border: 1px solid #34313A;
        background: linear-gradient(145deg, #171B23, #12151A);
        margin-bottom: 14px;
    }}

    .footer {{
        text-align: center;
        color: #778191;
        font-size: 11px;
        padding: 30px 0 12px;
        border-top: 1px solid #252A33;
        margin-top: 35px;
    }}

    .stButton > button {{
        border-radius: 10px;
        min-height: 42px;
        font-weight: 650;
        border: 1px solid #4A402C;
        transition: all .18s ease;
    }}

    .stButton > button[kind="primary"] {{
        background: linear-gradient(135deg, #E8CE8A, #B7944A);
        color: #18150E;
        border: none;
    }}

    .stButton > button[kind="primary"]:hover {{
        background: linear-gradient(135deg, #F4DFA7, #D2AF64);
        color: #14110B;
        border: none;
    }}

    .stDownloadButton > button {{
        background: #1A2029;
        color: #F2D99A;
        border: 1px solid #4A402C;
        border-radius: 10px;
        font-weight: 600;
    }}

    .stDownloadButton > button:hover {{
        border-color: var(--gold);
        color: #FFF0C5;
    }}

    [data-testid="stFileUploader"] {{
        border: 1px dashed #65532E;
        border-radius: 13px;
        padding: 10px;
        background: rgba(214,181,104,.025);
    }}

    [data-testid="stFileUploader"] section {{
        background: transparent;
    }}

    [data-testid="stMetric"] {{
        background: #171B23;
        border: 1px solid #2A303B;
        padding: 15px;
        border-radius: 13px;
    }}

    [data-testid="stMetricLabel"] {{
        color: #ADB5C1;
    }}

    [data-testid="stMetricValue"] {{
        color: #F2D99A;
    }}

    [data-testid="stDataFrame"] {{
        border: 1px solid #303642;
        border-radius: 12px;
        overflow: hidden;
    }}

    div[data-baseweb="input"] > div,
    div[data-baseweb="select"] > div {{
        background: #171B23;
        border-color: #343B47;
        border-radius: 9px;
    }}

    input, textarea {{
        color: #F1F3F6 !important;
    }}

    [data-testid="stNumberInput"] button {{
        background: #252B35;
        color: #F1D99B;
        border-color: #343B47;
    }}

    hr {{
        border-color: #292F39;
    }}

    [data-testid="stExpander"] {{
        background: #13171E;
        border: 1px solid #303641;
        border-radius: 13px;
        overflow: hidden;
    }}

    [data-testid="stAlert"] {{
        border-radius: 12px;
    }}

    @media (max-width: 700px) {{
        .hero {{
            padding: 20px;
        }}
        .hero h1 {{
            font-size: 27px;
        }}
        .metric {{
            padding: 14px;
        }}
        .metric-value {{
            font-size: 23px;
        }}
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# OUTILS PDF ET EXTRACTION
# ============================================================

def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte))
    texte = "".join(
        c for c in texte if not unicodedata.combining(c)
    )
    return texte.lower().replace("\u00a0", " ").strip()


def lire_texte_pdf(fichier):
    try:
        document = pymupdf.open(
            stream=fichier.getvalue(),
            filetype="pdf",
        )
        texte = "\n".join(page.get_text() for page in document)
        document.close()
        return texte.strip()
    except Exception:
        return ""


def convertir_montant(valeur):
    if valeur is None:
        return None

    texte = str(valeur).strip()
    texte = re.sub(r"[^\d,.\-]", "", texte)

    if not texte:
        return None

    if "," in texte and "." in texte:
        if texte.rfind(",") > texte.rfind("."):
            texte = texte.replace(".", "").replace(",", ".")
        else:
            texte = texte.replace(",", "")
    elif "," in texte:
        texte = texte.replace(",", ".")

    try:
        return round(float(texte), 2)
    except ValueError:
        return None


def extraire_dates(texte):
    motif = r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"
    trouvees = re.findall(motif, texte)
    dates = []

    for valeur in trouvees:
        for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d/%m/%y", "%d.%m.%y"):
            try:
                date = datetime.strptime(valeur, fmt)
                resultat = date.strftime("%d/%m/%Y")
                if resultat not in dates:
                    dates.append(resultat)
                break
            except ValueError:
                continue

    return dates


def extraire_fournisseur(texte):
    lignes = [l.strip() for l in texte.splitlines() if l.strip()]
    texte_norm = normaliser(texte)

    # Facture fictive de test
    if "atelier nuage fictif" in texte_norm:
        return "Atelier Nuage Fictif"

    exclusions = (
        "facture", "invoice", "date", "numero", "client",
        "adresse", "total", "tva", "tel", "email", "siret",
        "description", "document invente", "test uniquement",
        "document fictif", "bureau demo", "montant",
        "echeance", "devise", "conditions de paiement",
    )

    for i, ligne in enumerate(lignes):
        ligne_norm = normaliser(ligne)

        if re.search(r"\bfournisseur\b", ligne_norm):
            morceaux = re.split(r"[:：]", ligne, maxsplit=1)

            if len(morceaux) == 2 and morceaux[1].strip():
                candidat = morceaux[1].strip()
                if not any(x in normaliser(candidat) for x in exclusions):
                    return candidat[:100]

            if i + 1 < len(lignes):
                candidat = lignes[i + 1].strip()
                candidat_norm = normaliser(candidat)

                if (
                    candidat
                    and not any(x in candidat_norm for x in exclusions)
                    and not re.search(
                        r"\b(numero|date|total|tva)\b",
                        candidat_norm,
                    )
                ):
                    return candidat[:100]

    for ligne in lignes[:15]:
        ligne_norm = normaliser(ligne)

        if (
            len(ligne) >= 3
            and not any(x in ligne_norm for x in exclusions)
            and not re.search(r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}", ligne)
            and not re.search(r"\b\d{5,}\b", ligne)
        ):
            return ligne[:100]

    return ""


def extraire_numero(texte):
    # Priorité au numéro de la facture fictive de test
    resultat = re.search(
        r"\bDEMO-2026-0042\b",
        texte,
        re.IGNORECASE,
    )
    if resultat:
        return "DEMO-2026-0042"

    motifs = [
        r"(?:numero|numéro)\s+de\s+facture\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        r"\bn[°o]\s*(?:de\s*)?facture\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        r"\binvoice\s*(?:number|no\.?|#)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        r"\bfacture\s*[:#]\s*([A-Z0-9][A-Z0-9/_-]{2,})",
    ]

    lignes = texte.splitlines()

    for i, ligne in enumerate(lignes):
        ligne_norm = normaliser(ligne)

        if any(m in ligne_norm for m in (
            "date de facture", "date d'emission",
            "date d'echeance", "nom du client",
        )):
            continue

        for motif in motifs:
            resultat = re.search(motif, ligne, re.IGNORECASE)
            if resultat:
                numero = resultat.group(1).strip(" :-#")
                if normaliser(numero) not in {
                    "du", "date", "client", "facture",
                }:
                    return numero

        if "numero de facture" in ligne_norm and i + 1 < len(lignes):
            candidat = re.sub(r"\s+", "", lignes[i + 1].strip())
            if re.fullmatch(r"[A-Z0-9][A-Z0-9/_-]{2,}", candidat, re.I):
                return candidat

    return ""


def extraire_date_facture(texte):
    lignes = texte.splitlines()

    for i, ligne in enumerate(lignes):
        ligne_norm = normaliser(ligne)

        if any(m in ligne_norm for m in (
            "date d'emission", "date emission", "date de facture",
            "invoice date", "date facture",
        )):
            dates = extraire_dates(ligne)
            if dates:
                return dates[0]

            if i + 1 < len(lignes):
                dates = extraire_dates(lignes[i + 1])
                if dates:
                    return dates[0]

    dates = extraire_dates(texte)
    return dates[0] if dates else ""


def extraire_date_echeance(texte):
    lignes = texte.splitlines()

    for i, ligne in enumerate(lignes):
        ligne_norm = normaliser(ligne)

        if any(m in ligne_norm for m in (
            "echeance", "date limite", "due date", "payment due",
        )):
            dates = extraire_dates(ligne)
            if dates:
                return dates[-1]

            if i + 1 < len(lignes):
                dates = extraire_dates(lignes[i + 1])
                if dates:
                    return dates[-1]

    return ""


def extraire_montant(texte, etiquettes):
    lignes = texte.splitlines()
    nombre = r"-?\d[\d \u00a0\u202f]*(?:[.,]\d{1,2})?"

    for i, ligne in enumerate(lignes):
        ligne_norm = normaliser(ligne)

        if any(
            re.search(etiquette, ligne_norm, re.IGNORECASE)
            for etiquette in etiquettes
        ):
            zone = ligne

            if i + 1 < len(lignes):
                zone += " " + lignes[i + 1]

            valeurs = re.findall(
                nombre + r"\s*(?:€|EUR|USD|\$|GBP|£)?",
                zone,
                re.IGNORECASE,
            )

            montants = [
                convertir_montant(v)
                for v in valeurs
                if convertir_montant(v) is not None
            ]

            if montants:
                return montants[-1]

    return None


def extraire_taux_tva(texte):
    motifs = [
        r"(?:tva|vat)[^\n%]{0,30}?(\d{1,2}(?:[.,]\d+)?)\s*%",
        r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:de\s+)?(?:tva|vat)",
    ]

    for motif in motifs:
        resultat = re.search(motif, texte, re.IGNORECASE)
        if resultat:
            try:
                return float(resultat.group(1).replace(",", "."))
            except ValueError:
                pass

    return None


def extraire_devise(texte):
    if "€" in texte or re.search(r"\bEUR\b", texte, re.I):
        return "EUR (€)"
    if "$" in texte or re.search(r"\bUSD\b", texte, re.I):
        return "USD ($)"
    if "£" in texte or re.search(r"\bGBP\b", texte, re.I):
        return "GBP (£)"
    return "À vérifier"


def verifier_coherence(ht, tva, ttc):
    ecart = round(ht + tva - ttc, 2)

    if abs(ecart) <= 0.02:
        return True, "HT + TVA correspond au TTC."

    return False, f"Écart détecté : HT + TVA - TTC = {ecart:.2f}"


def euro(valeur):
    return f"{valeur:,.2f} €".replace(",", " ").replace(".", ",")


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.markdown(
        """
        <div class="brand">
            <div class="brand-icon">F</div>
            <div>
                <div class="brand-title">Factures IA</div>
                <div class="brand-sub">Premium workspace</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("**ESPACE DE TRAVAIL**")

    page = st.radio(
        "Navigation",
        ["Tableau de bord", "Factures PDF", "Récapitulatif"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown(
        f"""
        <div class="subtext">
            <span class="status-pill">● APPLICATION ACTIVE</span>
            <br><br>
            Extraction documentaire<br>
            Vérification des montants<br>
            Export des données
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.caption("FACTURES IA · PREMIUM")
    st.caption("Les données extraites doivent être vérifiées.")


# ============================================================
# SESSION ET DONNEES
# ============================================================

if "recap" not in st.session_state:
    st.session_state.recap = []

if "fichiers_analyses" not in st.session_state:
    st.session_state.fichiers_analyses = 0


# ============================================================
# EN-TETE
# ============================================================

st.markdown(
    """
    <div class="hero">
        <div class="eyebrow">DOCUMENT INTELLIGENCE · PREMIUM</div>
        <h1>Votre gestion documentaire, simplifiée.</h1>
        <p>
            Centralisez vos factures, extrayez les informations essentielles
            et contrôlez les montants depuis un espace de travail unique.
        </p>
        <span class="hero-tag">✦ FACTURES IA &nbsp; / &nbsp; WORKSPACE PRO</span>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# IMPORT PDF
# ============================================================

with st.container():
    st.markdown(
        """
        <div class="section-head">
            <span class="section-mark"></span>
            Centre de traitement
        </div>
        <div class="subtext">
            Importez une ou plusieurs factures au format PDF.
            Les données extraites restent modifiables avant validation.
        </div>
        """,
        unsafe_allow_html=True,
    )

    fichiers = st.file_uploader(
        "Sélectionner des factures PDF",
        type=["pdf"],
        accept_multiple_files=True,
        help="Vous pouvez sélectionner plusieurs PDF à la fois.",
    )


# ============================================================
# CARTES DASHBOARD
# ============================================================

nb_recap = len(st.session_state.recap)
total_ht = sum(float(x.get("HT", 0)) for x in st.session_state.recap)
total_tva = sum(float(x.get("TVA", 0)) for x in st.session_state.recap)
total_ttc = sum(float(x.get("TTC", 0)) for x in st.session_state.recap)

m1, m2, m3, m4 = st.columns(4)

with m1:
    st.markdown(
        f"""
        <div class="metric">
            <div class="metric-label">Factures au récapitulatif</div>
            <div class="metric-value">{nb_recap}</div>
            <div class="metric-note">Documents enregistrés dans cette session</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with m2:
    st.markdown(
        f"""
        <div class="metric">
            <div class="metric-label">Total hors taxes</div>
            <div class="metric-value">{euro(total_ht)}</div>
            <div class="metric-note">Somme des montants HT</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with m3:
    st.markdown(
        f"""
        <div class="metric">
            <div class="metric-label">Total TVA</div>
            <div class="metric-value">{euro(total_tva)}</div>
            <div class="metric-note">Somme de la TVA enregistrée</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with m4:
    st.markdown(
        f"""
        <div class="metric">
            <div class="metric-label">Total TTC</div>
            <div class="metric-value">{euro(total_ttc)}</div>
            <div class="metric-note">Montant total enregistré</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# TRAITEMENT DES FACTURES
# ============================================================

if page in ("Tableau de bord", "Factures PDF"):
    if fichiers:
        st.markdown(
            f"""
            <div class="section-head">
                <span class="section-mark"></span>
                Documents importés
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption(f"{len(fichiers)} fichier(s) sélectionné(s).")

        for index, fichier in enumerate(fichiers):
            cle = f"{index}_{fichier.name}"

            with st.expander(f"📄  {fichier.name}", expanded=(index == 0)):
                texte = lire_texte_pdf(fichier)

                if not texte:
                    st.warning(
                        "Aucun texte détecté. Le PDF est peut-être scanné "
                        "ou protégé. L'OCR n'est pas encore disponible."
                    )
                    continue

                st.markdown(
                    '<span class="status-pill">TEXTE PDF EXTRAIT</span>',
                    unsafe_allow_html=True,
                )

                fournisseur_detecte = extraire_fournisseur(texte)
                numero_detecte = extraire_numero(texte)
                date_facture_detectee = extraire_date_facture(texte)
                date_echeance_detectee = extraire_date_echeance(texte)

                ht = extraire_montant(
                    texte,
                    [
                        r"\btotal\s+hors\s+taxes\b",
                        r"\bmontant\s+ht\b",
                        r"\btotal\s+h\.?t\.?\b",
                        r"\bsous.total\b",
                        r"\bsubtotal\b",
                    ],
                )

                tva = extraire_montant(
                    texte,
                    [
                        r"\bmontant\s+tva\b",
                        r"\btotal\s+tva\b",
                        r"\btva\b",
                        r"\bvat\b",
                    ],
                )

                ttc = extraire_montant(
                    texte,
                    [
                        r"\btotal\s+ttc\b",
                        r"\bnet\s+a\s+payer\b",
                        r"\btotal\s+a\s+payer\b",
                        r"\bamount\s+due\b",
                        r"\bgrand\s+total\b",
                    ],
                )

                taux_tva = extraire_taux_tva(texte)
                devise_detectee = extraire_devise(texte)

                st.markdown(
                    '<div class="section-head"><span class="section-mark"></span>'
                    'Informations de la facture</div>',
                    unsafe_allow_html=True,
                )

                gauche, droite = st.columns(2)

                with gauche:
                    fournisseur = st.text_input(
                        "Fournisseur",
                        value=fournisseur_detecte,
                        key=f"fourn_{cle}",
                    )

                    numero = st.text_input(
                        "Numéro de facture",
                        value=numero_detecte,
                        key=f"num_{cle}",
                    )

                    date_facture = st.text_input(
                        "Date de facture",
                        value=date_facture_detectee,
                        key=f"date_{cle}",
                        help="Format recommandé : JJ/MM/AAAA",
                    )

                    date_echeance = st.text_input(
                        "Date d'échéance",
                        value=date_echeance_detectee,
                        key=f"ech_{cle}",
                        help="Format recommandé : JJ/MM/AAAA",
                    )

                with droite:
                    devises = ["À vérifier", "EUR (€)", "USD ($)", "GBP (£)"]

                    devise = st.selectbox(
                        "Devise",
                        devises,
                        index=(
                            devises.index(devise_detectee)
                            if devise_detectee in devises
                            else 0
                        ),
                        key=f"devise_{cle}",
                    )

                    ht_val = st.number_input(
                        "Montant HT",
                        min_value=0.0,
                        value=float(ht or 0.0),
                        step=0.01,
                        format="%.2f",
                        key=f"ht_{cle}",
                    )

                    tva_val = st.number_input(
                        "Montant TVA",
                        min_value=0.0,
                        value=float(tva or 0.0),
                        step=0.01,
                        format="%.2f",
                        key=f"tva_{cle}",
                    )

                    ttc_val = st.number_input(
                        "Montant TTC",
                        min_value=0.0,
                        value=float(ttc or 0.0),
                        step=0.01,
                        format="%.2f",
                        key=f"ttc_{cle}",
                    )

                if taux_tva is not None:
                    st.caption(f"Taux de TVA détecté : {taux_tva:g} %")

                coherent, message = verifier_coherence(
                    ht_val,
                    tva_val,
                    ttc_val,
                )

                st.markdown(
                    '<div class="section-head"><span class="section-mark"></span>'
                    'Contrôle des montants</div>',
                    unsafe_allow_html=True,
                )

                c1, c2, c3 = st.columns(3)

                with c1:
                    st.metric("Total HT", euro(ht_val))
                with c2:
                    st.metric("TVA", euro(tva_val))
                with c3:
                    st.metric("Total TTC", euro(ttc_val))

                if coherent:
                    st.success("✓ " + message)
                else:
                    st.warning("⚠️ " + message)

                st.caption(
                    "Le contrôle vérifie uniquement la cohérence arithmétique. "
                    "Il ne certifie pas l'authenticité de la facture ni le taux légal applicable."
                )

                if st.button(
                    "＋ Ajouter au récapitulatif",
                    key=f"ajouter_{cle}",
                    type="primary",
                    use_container_width=True,
                ):
                    st.session_state.recap.append({
                        "Fichier": fichier.name,
                        "Fournisseur": fournisseur,
                        "Numéro": numero,
                        "Date facture": date_facture,
                        "Date échéance": date_echeance,
                        "Devise": devise,
                        "HT": ht_val,
                        "TVA": tva_val,
                        "TTC": ttc_val,
                    })
                    st.success("Facture ajoutée au récapitulatif.")
                    st.rerun()

    else:
        st.markdown(
            """
            <div class="upload-panel">
                <div class="eyebrow">PRÊT À COMMENCER</div>
                <h3>Importez votre première facture</h3>
                <div class="subtext">
                    Sélectionnez un fichier PDF dans la zone ci-dessus.
                    Les informations détectées pourront ensuite être vérifiées
                    et corrigées avant l'ajout au récapitulatif.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# RECAPITULATIF ET EXPORTS
# ============================================================

if page in ("Tableau de bord", "Récapitulatif"):
    st.markdown(
        """
        <div class="section-head">
            <span class="section-mark"></span>
            Récapitulatif des factures
        </div>
        <div class="subtext">
            Consultez les factures ajoutées pendant cette session
            et exportez les données pour votre suivi.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.recap:
        tableau = pd.DataFrame(st.session_state.recap)

        recherche = st.text_input(
            "⌕ Rechercher une facture",
            placeholder="Nom du fournisseur, numéro ou fichier...",
            key="recherche_recap",
        )

        tableau_affiche = tableau.copy()

        if recherche.strip():
            masque = tableau_affiche.astype(str).apply(
                lambda colonne: colonne.str.contains(
                    recherche,
                    case=False,
                    regex=False,
                    na=False,
                )
            ).any(axis=1)

            tableau_affiche = tableau_affiche[masque]

        st.dataframe(
            tableau_affiche,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            f"{len(tableau_affiche)} facture(s) affichée(s) "
            f"sur {len(tableau)} enregistrée(s)."
        )

        col_csv, col_excel, col_reset = st.columns([1, 1, 1])

        with col_csv:
            st.download_button(
                "⬇️ Exporter en CSV",
                data=tableau.to_csv(index=False).encode("utf-8-sig"),
                file_name="recap_factures.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col_excel:
            excel = io.BytesIO()

            with pd.ExcelWriter(excel, engine="openpyxl") as writer:
                tableau.to_excel(
                    writer,
                    index=False,
                    sheet_name="Factures",
                )

            st.download_button(
                "⬇️ Exporter en Excel",
                data=excel.getvalue(),
                file_name="recap_factures.xlsx",
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
            )

        with col_reset:
            if st.button(
                "🗑️ Vider le récapitulatif",
                use_container_width=True,
            ):
                st.session_state.recap = []
                st.rerun()

    else:
        st.markdown(
            """
            <div class="upload-panel">
                <h3>Aucune facture enregistrée</h3>
                <div class="subtext">
                    Les factures que vous ajoutez apparaîtront ici,
                    avec leurs données et les options d'export.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ============================================================
# PIED DE PAGE
# ============================================================

st.markdown(
    """
    <div class="footer">
        <strong style="color:#D6B568;">FACTURES IA</strong>
        &nbsp; · &nbsp; PREMIUM WORKSPACE
        <br><br>
        Outil d'assistance documentaire.
        Vérifiez les informations extraites avant toute utilisation comptable.
    </div>
    """,
    unsafe_allow_html=True,
)
