"""
app/services/query_processor.py

Rule-based query understanding — zero LLM cost.

Two responsibilities:

1. IntentSignals  — extract structured signals from the raw query string:
     sort_hint     "upcoming" / "latest" → deadline sort
     free_hint     "free" / "no fee" / "zero cost"
     year_hint     explicit year like "2025"
     month_hint    explicit month like "May"
     country_hints list of countries mentioned
     category_hint inferred category

2. expand_query  — enrich the raw string before BM25/hybrid retrieval:
     Layer 1 — noise stripping        remove "best", "top", "popular"
     Layer 2 — phrase expansions      "MBBS entrance" → "medical NEET AIIMS India"
     Layer 3 — synonym injections     "doctor" → "medical NEET AIIMS MBBS"
     Layer 4 — acronym expansions     "GRE" → "GRE Graduate Record Examination"
     Layer 5 — region+domain combos   "India engineering" → "JEE GATE IIT"
     Layer 6 — category append        append inferred category string
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Layer 2: Phrase-level expansions ──────────────────────────────────────
# Checked before single-word synonyms — longer matches take priority.
# Format: (compiled_pattern, expansion_string)
PHRASE_EXPANSIONS: list[tuple[re.Pattern, str]] = [

    # Medical / MBBS
    (re.compile(r'\bmbbs\s+entrance\b',   re.I), "medical admissions India NEET AIIMS JIPMER undergraduate"),
    (re.compile(r'\bmedical\s+entrance\b', re.I), "medical admissions NEET AIIMS JIPMER India undergraduate"),
    (re.compile(r'\bbecome\s+a\s+doctor\b',re.I), "medical admissions NEET AIIMS MBBS undergraduate India"),
    (re.compile(r'\bstudy\s+medicine\b',   re.I), "medical admissions MBBS NEET AIIMS undergraduate"),
    (re.compile(r'\bmedical\s+school\b',   re.I), "medical admissions MCAT NEET USMLE MBBS"),
    (re.compile(r'\bdoctor\s+entrance\b',  re.I), "NEET AIIMS JIPMER medical admissions India"),
    (re.compile(r'\bmedical\s+college\b',  re.I), "NEET AIIMS JIPMER medical admissions India undergraduate"),
    (re.compile(r'\bmed\s+school\b',       re.I), "MCAT USMLE medical admissions USA"),
    (re.compile(r'\bpg\s+medical\b',       re.I), "NEET PG postgraduate medical India AIIMS PG"),
    (re.compile(r'\bmedical\s+pg\b',       re.I), "NEET PG postgraduate medical India AIIMS PG"),

    # Engineering
    (re.compile(r'\beng(?:ineering)?\s+entrance\b', re.I), "engineering admissions JEE IIT India undergraduate"),
    (re.compile(r'\biit\s+entrance\b',     re.I), "JEE Advanced IIT engineering admissions India"),
    (re.compile(r'\bbecome\s+an?\s+engineer\b', re.I), "engineering admissions JEE GATE IIT India"),
    (re.compile(r'\bcomputer\s+science\s+entrance\b', re.I), "engineering JEE GATE IIT computer science India"),
    (re.compile(r'\bengg?\s+college\b',    re.I), "JEE engineering admissions India IIT NIT undergraduate"),

    # MBA / Business
    (re.compile(r'\bmba\s+entrance\b',     re.I), "MBA business admissions CAT GMAT XAT SNAP India"),
    (re.compile(r'\biim\s+admission\b',    re.I), "CAT IIM MBA business admissions India"),
    (re.compile(r'\bbusiness\s+school\b',  re.I), "MBA GMAT CAT business admissions"),
    (re.compile(r'\bbecome\s+an?\s+mba\b', re.I), "MBA CAT GMAT business management India"),
    (re.compile(r'\bmanagement\s+entrance\b', re.I), "CAT XAT SNAP NMAT GMAT MBA management India"),

    # Law
    (re.compile(r'\blaw\s+school\s+admission\b', re.I), "LSAT law school admissions USA"),
    (re.compile(r'\blaw\s+entrance\b',     re.I), "CLAT law India NLU undergraduate"),
    (re.compile(r'\bnlu\s+entrance\b',     re.I), "CLAT NLU law India undergraduate"),
    (re.compile(r'\bbecome\s+a\s+lawyer\b',re.I), "CLAT LSAT law school admissions"),
    (re.compile(r'\bpractice\s+law\b',     re.I), "bar exam LSAT law licensing"),

    # Language
    (re.compile(r'\benglish\s+proficiency\b',  re.I), "IELTS TOEFL PTE English language proficiency"),
    (re.compile(r'\bstudy\s+abroad\b',     re.I), "IELTS TOEFL GRE GMAT graduate language proficiency"),
    (re.compile(r'\bwork\s+abroad\b',      re.I), "IELTS TOEFL OET language proficiency"),
    (re.compile(r'\bvisa\s+english\b',     re.I), "IELTS TOEFL PTE language proficiency immigration"),
    (re.compile(r'\bjapanese\s+language\b',re.I), "JLPT Japanese language proficiency N1 N2 N3"),
    (re.compile(r'\bfrench\s+language\b',  re.I), "DELF DALF French language proficiency"),
    (re.compile(r'\bspanish\s+language\b', re.I), "DELE SIELE Spanish language proficiency"),
    (re.compile(r'\bgerman\s+language\b',  re.I), "TestDaF Goethe German language proficiency"),
    (re.compile(r'\bchinese\s+language\b', re.I), "HSK Chinese language proficiency Mandarin"),

    # Government / Civil Services
    (re.compile(r'\bgovernment\s+job\b',   re.I), "civil services UPSC IAS IPS government India"),
    (re.compile(r'\bcivil\s+service\b',    re.I), "UPSC IAS IPS civil services government India"),
    (re.compile(r'\bias\s+exam\b',         re.I), "UPSC civil services IAS government India"),

    # Finance / Accounting
    (re.compile(r'\bchartered\s+accountant\b', re.I), "CA ICAI accounting CPA ACCA finance"),
    (re.compile(r'\bfinancial\s+analyst\b',    re.I), "CFA CMA FRM finance investment"),
    (re.compile(r'\bca\s+exam\b',          re.I), "CA ICAI chartered accountant India accounting"),

    # Medical licensing
    (re.compile(r'\bforeign\s+medical\b',  re.I), "FMGE USMLE PLAB AMC foreign medical graduate licensing"),
    (re.compile(r'\bdoctor\s+in\s+usa\b',  re.I), "USMLE ECFMG medical licensing USA"),
    (re.compile(r'\bdoctor\s+in\s+uk\b',   re.I), "PLAB GMC medical licensing UK"),
    (re.compile(r'\bdoctor\s+in\s+australia\b', re.I), "AMC medical licensing Australia"),

    # Nursing
    (re.compile(r'\bnurse\s+license\b',    re.I), "NCLEX nursing registration RN LPN"),
    (re.compile(r'\bnursing\s+exam\b',     re.I), "NCLEX nursing RN LPN licensing"),

    # IT / Tech certifications
    (re.compile(r'\bcloud\s+certification\b', re.I), "AWS Azure GCP cloud professional certification"),
    (re.compile(r'\bit\s+certification\b', re.I), "AWS CISSP CompTIA PMP professional certification"),
    (re.compile(r'\bcybersecurity\s+cert\b', re.I), "CISSP CEH CompTIA Security+ cybersecurity"),

    # Undergraduate abroad
    (re.compile(r'\bundergrad\s+usa\b',    re.I), "SAT ACT undergraduate college admissions USA"),
    (re.compile(r'\bcollege\s+usa\b',      re.I), "SAT ACT undergraduate admissions USA"),
    (re.compile(r'\buniversity\s+uk\b',    re.I), "A-levels UCAS undergraduate admissions UK"),
    (re.compile(r'\buniversity\s+australia\b', re.I), "IELTS undergraduate admissions Australia"),
    (re.compile(r'\buniversity\s+abroad\b',re.I), "IELTS TOEFL SAT GRE undergraduate graduate admissions"),

    # Graduate admissions
    (re.compile(r'\bphd\s+admission\b',    re.I), "GRE graduate admissions PhD research"),
    (re.compile(r'\bmasters\s+admission\b',re.I), "GRE GMAT graduate admissions masters"),
    (re.compile(r'\bms\s+usa\b',           re.I), "GRE graduate admissions masters USA"),
    (re.compile(r'\bmasters?\s+in\s+usa\b',re.I), "GRE graduate admissions USA IELTS TOEFL"),
]

# ── Layer 3: Single-word synonym injections ───────────────────────────────
# Maps a single domain/goal word to relevant exam vocabulary.
# Applied as whole-word match only (word boundaries).
SYNONYM_INJECTIONS: dict[str, str] = {
    # Medical
    "doctor":       "medical NEET AIIMS MBBS medical admissions",
    "doctors":      "medical NEET AIIMS MBBS medical admissions",
    "physician":    "medical USMLE PLAB NEET licensing",
    "surgeon":      "medical USMLE PLAB licensing",
    "dentist":      "medical dental BDS dental admissions",
    "pharmacist":   "pharmacy D.Pharm B.Pharm pharmaceutical",
    "nurse":        "nursing NCLEX RN LPN healthcare",
    "nursing":      "NCLEX RN nursing registration healthcare",
    "mbbs":         "medical admissions NEET AIIMS India undergraduate",
    "medicine":     "medical MBBS NEET AIIMS undergraduate",

    # Engineering
    "engineer":     "engineering JEE GATE IIT undergraduate admissions",
    "engineers":    "engineering JEE GATE IIT undergraduate admissions",
    "btech":        "engineering JEE IIT undergraduate India admissions",
    "iit":          "JEE Advanced IIT engineering India",
    "nit":          "JEE Mains NIT engineering India undergraduate",

    # Business / MBA
    "mba":          "MBA business management CAT GMAT XAT admissions",
    "iim":          "CAT IIM MBA business India admissions",
    "management":   "MBA CAT GMAT management admissions",
    "entrepreneur": "MBA business management GMAT",

    # Law
    "lawyer":       "law CLAT LSAT bar exam LLB admissions",
    "lawyers":      "law CLAT LSAT bar exam LLB admissions",
    "advocate":     "law CLAT bar exam LLB India",
    "attorney":     "bar exam LSAT law USA licensing",
    "llb":          "law CLAT undergraduate India admissions",

    # Finance / Accounting
    "accountant":   "CPA ACCA CA ICAI accounting finance",
    "finance":      "CFA ACCA CPA FRM finance investment accounting",
    "investment":   "CFA FRM finance investment",
    "banking":      "CFA ACCA finance banking professional",
    "audit":        "ACCA CPA CA audit accounting",
    "ca":           "CA ICAI chartered accountant India accounting",

    # IT / Tech
    "programmer":   "software engineering JEE GATE computer science",
    "developer":    "software engineering GATE computer science certification",
    "cloud":        "AWS Azure GCP cloud certification professional",
    "cybersecurity":"CISSP CEH CompTIA Security+ cybersecurity",
    "devops":       "AWS Azure DevOps cloud certification professional",

    # Language proficiency
    "english":      "IELTS TOEFL PTE OET English language proficiency",
    "japanese":     "JLPT Japanese N1 N2 N3 language proficiency",
    "french":       "DELF DALF French language proficiency",
    "german":       "TestDaF Goethe German language proficiency",
    "spanish":      "DELE SIELE Spanish language proficiency",
    "mandarin":     "HSK Chinese language proficiency",
    "chinese":      "HSK Chinese language Mandarin proficiency",
    "korean":       "TOPIK Korean language proficiency",

    # Civil services
    "ias":          "UPSC civil services IAS government India",
    "ips":          "UPSC civil services IPS government India",
    "bureaucrat":   "UPSC civil services IAS government India",
    "government":   "UPSC civil services government India public service",

    # Study destination
    "usa":          "USA United States GRE GMAT SAT IELTS TOEFL admissions",
    "uk":           "UK United Kingdom A-levels IELTS PLAB admissions",
    "australia":    "Australia IELTS AMC nursing admissions",
    "canada":       "Canada IELTS MCAT LSAT admissions",
    "germany":      "Germany TestDaF Goethe undergraduate graduate admissions",
    "abroad":       "IELTS TOEFL GRE GMAT study international admissions",
    "language":     "IELTS TOEFL PTE language proficiency test",

    # Difficulty-related
    "competitive":  "hard very hard extremely hard competitive admissions",
    "toughest":     "extremely hard very hard competitive JEE UPSC USMLE",
    "hardest":      "extremely hard very hard competitive JEE UPSC USMLE",
    "easiest":      "medium easy language proficiency certification",

    # Cost-related
    "free":         "free no cost zero fee language proficiency certification",
    "cheap":        "low cost affordable free language certification",
    "affordable":   "low cost affordable certification language",
    "scholarship":  "free no cost merit scholarship competitive admissions",

    # Misc intent
    "upcoming":     "deadline registration closing soon",
    "certification":"professional certification AWS CFA ACCA CompTIA",
    "licensing":    "licensing USMLE PLAB NCLEX FMGE bar exam professional",
    "postgraduate": "graduate admissions GRE GMAT masters PhD",
    "undergraduate":"undergraduate admissions SAT JEE NEET A-levels",
}

# ── Layer 5: Region + domain combinations ────────────────────────────────
# When the query mentions a specific region and a domain, inject the most
# relevant exam names for that combination.
REGION_DOMAIN_COMBOS: list[tuple[re.Pattern, re.Pattern, str]] = [
    # India + domain
    (re.compile(r'\bindia\b', re.I),
     re.compile(r'\b(medical|doctor|mbbs|medicine)\b', re.I),
     "NEET AIIMS JIPMER COMEDK medical admissions India undergraduate"),
    (re.compile(r'\bindia\b', re.I),
     re.compile(r'\b(engineering|btech|iit|nit)\b', re.I),
     "JEE Advanced JEE Mains GATE IIT NIT engineering India"),
    (re.compile(r'\bindia\b', re.I),
     re.compile(r'\b(mba|management|business)\b', re.I),
     "CAT XAT SNAP NMAT IIM MBA India management"),
    (re.compile(r'\bindia\b', re.I),
     re.compile(r'\b(law|legal|lawyer)\b', re.I),
     "CLAT AILET NLU law India undergraduate"),
    (re.compile(r'\bindia\b', re.I),
     re.compile(r'\b(civil service|government|ias|ips)\b', re.I),
     "UPSC IAS IPS civil services government India"),

    # USA + domain
    (re.compile(r'\busa\b|\bunited states\b', re.I),
     re.compile(r'\b(medical|doctor|medicine)\b', re.I),
     "MCAT USMLE medical school admissions USA licensing"),
    (re.compile(r'\busa\b|\bunited states\b', re.I),
     re.compile(r'\b(law|legal|lawyer)\b', re.I),
     "LSAT bar exam law school USA attorney"),
    (re.compile(r'\busa\b|\bunited states\b', re.I),
     re.compile(r'\b(college|undergraduate|university)\b', re.I),
     "SAT ACT undergraduate college admissions USA"),
    (re.compile(r'\busa\b|\bunited states\b', re.I),
     re.compile(r'\b(graduate|masters|phd|ms)\b', re.I),
     "GRE GMAT graduate admissions USA TOEFL IELTS"),

    # UK + domain
    (re.compile(r'\buk\b|\bunited kingdom\b', re.I),
     re.compile(r'\b(medical|doctor|medicine)\b', re.I),
     "PLAB UKMLA medical licensing UK GMC"),
    (re.compile(r'\buk\b|\bunited kingdom\b', re.I),
     re.compile(r'\b(undergraduate|college|university)\b', re.I),
     "A-levels UCAS undergraduate UK"),

    # Australia + domain
    (re.compile(r'\baustralia\b', re.I),
     re.compile(r'\b(medical|doctor|medicine)\b', re.I),
     "AMC medical licensing Australia"),
    (re.compile(r'\baustralia\b', re.I),
     re.compile(r'\b(nursing|nurse)\b', re.I),
     "NCLEX AHPRA nursing registration Australia"),

    # China + domain
    (re.compile(r'\bchina\b|\bchinese\b', re.I),
     re.compile(r'\b(undergraduate|college|university)\b', re.I),
     "Gaokao undergraduate China admissions"),

    # Japan + domain
    (re.compile(r'\bjapan\b|\bjapanese\b', re.I),
     re.compile(r'\b(language|study|work)\b', re.I),
     "JLPT Japanese language proficiency N1 N2 N3 N4"),

    # Global / Finance
    (re.compile(r'\bglobal\b|\binternational\b', re.I),
     re.compile(r'\b(finance|investment|accounting)\b', re.I),
     "CFA ACCA CPA FRM global finance certification"),
]

# ── Layer 1: Acronym expansions (enlarged) ────────────────────────────────
ACRONYM_MAP: dict[str, str] = {
    # Graduate admissions (USA/Global)
    "gre":    "GRE Graduate Record Examination graduate admissions",
    "gmat":   "GMAT Graduate Management Admission Test MBA business",
    "sat":    "SAT college admissions undergraduate USA College Board",
    "act":    "ACT college admissions undergraduate USA",
    "lsat":   "LSAT Law School Admission Test law school USA",
    "mcat":   "MCAT Medical College Admission Test medical school USA",
    "ap":     "AP Advanced Placement undergraduate college USA secondary",

    # English language
    "ielts":  "IELTS International English Language Testing System proficiency",
    "toefl":  "TOEFL Test English Foreign Language proficiency USA",
    "pte":    "PTE Pearson Test English language proficiency Australia UK",
    "oet":    "OET Occupational English Test healthcare professionals",
    "toeic":  "TOEIC Test English International Communication workplace",
    "duolingo": "Duolingo English Test DET online language proficiency",

    # Other language proficiency
    "jlpt":   "JLPT Japanese Language Proficiency Test N1 N2 N3 N4 N5",
    "delf":   "DELF Diplôme Études Langue Française French proficiency",
    "dalf":   "DALF Diplôme Approfondi Langue Française advanced French",
    "dele":   "DELE Diplomas Español Lengua Extranjera Spanish proficiency",
    "siele":  "SIELE Servicio Internacional Evaluación Lengua Española Spanish",
    "hsk":    "HSK Hanyu Shuiping Kaoshi Chinese language proficiency Mandarin",
    "topik":  "TOPIK Test Proficiency Korean language South Korea",
    "goethe": "Goethe-Zertifikat German language proficiency",
    "testdaf":"TestDaF Test Deutsch als Fremdsprache German university",

    # India — Engineering
    "jee":    "JEE Joint Entrance Examination engineering India IIT NIT undergraduate",
    "gate":   "GATE Graduate Aptitude Test Engineering India PSU masters",
    "bitsat": "BITSAT BITS Pilani engineering India undergraduate",
    "viteee": "VITEEE VIT University engineering India undergraduate",
    "comedk": "COMEDK engineering medical Karnataka India undergraduate",
    "kcet":   "KCET Karnataka Common Entrance Test engineering medical India",
    "mhtcet": "MHTCET Maharashtra engineering pharmacy India undergraduate",

    # India — Medical
    "neet":   "NEET National Eligibility Entrance Test medical India undergraduate",
    "aiims":  "AIIMS All India Institute Medical Sciences medical India",
    "jipmer": "JIPMER Jawaharlal Institute Postgraduate Medical Education Research India",
    "fmge":   "FMGE Foreign Medical Graduates Examination India licensing",

    # India — Management
    "cat":    "CAT Common Admission Test MBA IIM business India management",
    "xat":    "XAT Xavier Aptitude Test MBA India management XLRI",
    "snap":   "SNAP Symbiosis National Aptitude Test MBA India Symbiosis",
    "nmat":   "NMAT NMIMS Management Aptitude Test MBA India",
    "cmat":   "CMAT Common Management Admission Test MBA India",
    "mat":    "MAT Management Aptitude Test MBA India AIMA",
    "atma":   "ATMA AIMS Test Management Admissions MBA India",
    "iift":   "IIFT Indian Institute Foreign Trade MBA India international business",

    # India — Law
    "clat":   "CLAT Common Law Admission Test NLU law India undergraduate",
    "ailet":  "AILET All India Law Entrance Test NLU Delhi law India",

    # India — Civil Services
    "upsc":   "UPSC Union Public Service Commission civil services IAS IPS India government",
    "ias":    "IAS Indian Administrative Service UPSC civil services India",

    # India — Other
    "cuet":   "CUET Common University Entrance Test India undergraduate central university",
    "nda":    "NDA National Defence Academy India defence military",

    # USA — Medical licensing
    "usmle":  "USMLE United States Medical Licensing Examination doctor USA",
    "nclex":  "NCLEX National Council Licensure Examination nursing RN LPN USA",
    "comlex": "COMLEX Comprehensive Osteopathic Medical Licensing USA",

    # UK/International Medical
    "plab":   "PLAB Professional Linguistic Assessments Board medical UK GMC licensing",
    "amc":    "AMC Australian Medical Council medical licensing Australia",

    # Finance / Accounting
    "cfa":    "CFA Chartered Financial Analyst finance investment global",
    "cpa":    "CPA Certified Public Accountant accounting USA",
    "acca":   "ACCA Association Chartered Certified Accountants finance UK global",
    "cma":    "CMA Certified Management Accountant accounting finance IMA",
    "frm":    "FRM Financial Risk Manager risk finance GARP",
    "ca":     "CA Chartered Accountant ICAI India accounting finance",
    "icai":   "ICAI Institute Chartered Accountants India CA accounting",

    # Project Management / IT
    "pmp":    "PMP Project Management Professional PMI certification",
    "aws":    "AWS Amazon Web Services cloud certification professional",
    "cissp":  "CISSP Certified Information Systems Security Professional cybersecurity",
    "ceh":    "CEH Certified Ethical Hacker cybersecurity EC-Council",
    "ccna":   "CCNA Cisco Certified Network Associate networking",

    # Bar exams
    "ube":    "UBE Uniform Bar Exam law USA attorney licensing",
    "bar":    "bar exam law licensing attorney USA UBE",

    # Secondary education
    "ib":     "IB International Baccalaureate secondary school diploma",
    "gcse":   "GCSE General Certificate Secondary Education UK secondary",
    "igcse":  "IGCSE International General Certificate Secondary Education Cambridge",

    # Other global
    "gre":    "GRE Graduate Record Examination graduate admissions USA",
}

# ── Country mentions ──────────────────────────────────────────────────────
_COUNTRIES = {
    "india", "china", "usa", "united states", "uk", "united kingdom",
    "australia", "canada", "germany", "france", "japan", "singapore",
    "brazil", "south korea", "uae", "dubai", "south africa", "nigeria",
    "kenya", "pakistan", "bangladesh", "sri lanka", "nepal", "iran",
    "indonesia", "malaysia", "philippines", "vietnam", "thailand",
    "new zealand", "ireland", "netherlands", "sweden", "switzerland",
}

# ── Domain → category mapping ─────────────────────────────────────────────
_DOMAIN_CATEGORY: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b(medical|mbbs|doctor|medicine|healthcare|neet|mcat|usmle|plab|amc|fmge)\b', re.I),
     "Medical Admissions"),
    (re.compile(r'\b(mba|business|management|gmat|cat\b|xat|snap|nmat|cmat|iim)\b', re.I),
     "Business School"),
    (re.compile(r'\b(law|legal|lsat|bar exam|attorney|llb|clat|nlu)\b', re.I),
     "Law School"),
    (re.compile(r'\b(engineering|iit|jee|gate|btech|beng|bitsat)\b', re.I),
     "Engineering Admissions"),
    (re.compile(r'\b(english|ielts|toefl|pte|oet|toeic|duolingo|language proficiency)\b', re.I),
     "Language Proficiency"),
    (re.compile(r'\b(japanese|jlpt|french|delf|dalf|spanish|dele|german|testdaf|chinese|hsk|korean|topik)\b', re.I),
     "Language Proficiency"),
    (re.compile(r'\b(graduate|phd|masters|gre|postgrad)\b', re.I),
     "Graduate Admissions"),
    (re.compile(r'\b(undergraduate|bachelors|college|sat\b|act\b|a.level|gaokao|cuet)\b', re.I),
     "Undergraduate Admissions"),
    (re.compile(r'\b(finance|cfa|cpa|acca|cma|frm|accounting|investment|ca\b|icai)\b', re.I),
     "Finance Certification"),
    (re.compile(r'\b(civil service|government|upsc|ias|ips|public service)\b', re.I),
     "Government"),
    (re.compile(r'\b(cloud|aws|azure|devops|cissp|ceh|ccna|pmp|certification|it\b)\b', re.I),
     "Professional Certification"),
    (re.compile(r'\b(nursing|nclex|rn|lpn|healthcare worker)\b', re.I),
     "Medical Licensing"),
    (re.compile(r'\b(usmle|plab|fmge|amc|medical licens)\b', re.I),
     "Medical Licensing"),
    (re.compile(r'\b(secondary|high school|gcse|igcse|a.level|ib diploma|abitur|baccalaur)\b', re.I),
     "Secondary Education"),
]

# ── Sort signals ──────────────────────────────────────────────────────────
_DEADLINE_HINTS   = re.compile(r'\b(upcoming|soon|nearest|next|deadline|closing|due|register|apply)\b', re.I)
_COST_HINTS       = re.compile(r'\b(cheap|cheapest|affordable|low.cost|inexpensive|budget)\b', re.I)
_FREE_HINTS       = re.compile(r'\b(free|no.fee|zero.cost|no.charge|without.fee|waiver)\b', re.I)
_DIFFICULTY_HINTS = re.compile(r'\b(hardest|toughest|most.difficult|easiest|simplest)\b', re.I)

# ── Noise words to strip before BM25 ─────────────────────────────────────
_NOISE = re.compile(
    r'\b(best|top|popular|famous|good|great|leading|major|important|recent'
    r'|list|all|every|what|which|how|tell|give|show|find|get|want|need)\b',
    re.I
)

# ── Year and month extraction ─────────────────────────────────────────────
_YEAR_RE  = re.compile(r'\b(202[4-9]|203[0-2])\b')
_MONTHS   = {'january','february','march','april','may','june','july','august',
             'september','october','november','december',
             'jan','feb','mar','apr','jun','jul','aug','sep','oct','nov','dec'}
_MONTH_RE = re.compile(
    r'\b(january|february|march|april|may|june|july|august|september|'
    r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b',
    re.I
)
_ABBR_TO_FULL = {
    'jan':'January','feb':'February','mar':'March','apr':'April',
    'jun':'June','jul':'July','aug':'August','sep':'September',
    'oct':'October','nov':'November','dec':'December',
}


# ── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class IntentSignals:
    sort_hint:      str        = "relevance"
    free_hint:      bool       = False
    year_hint:      int | None = None
    month_hint:     str | None = None
    country_hints:  list[str]  = field(default_factory=list)
    category_hint:  str | None = None
    acronyms_found: list[str]  = field(default_factory=list)
    phrase_matches: list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sort_hint":      self.sort_hint,
            "free_hint":      self.free_hint,
            "year_hint":      self.year_hint,
            "month_hint":     self.month_hint,
            "country_hints":  self.country_hints,
            "category_hint":  self.category_hint,
            "acronyms_found": self.acronyms_found,
            "phrase_matches": self.phrase_matches,
        }


def extract_intent(query: str) -> IntentSignals:
    """Extract structured signals from a raw query string."""
    signals = IntentSignals()
    q_lower  = query.lower()

    # Sort hint
    if _FREE_HINTS.search(query):
        signals.free_hint = True
        signals.sort_hint = "cost_asc"
    elif _DEADLINE_HINTS.search(query):
        signals.sort_hint = "deadline"
    elif _COST_HINTS.search(query):
        signals.sort_hint = "cost_asc"
    elif _DIFFICULTY_HINTS.search(query):
        signals.sort_hint = "difficulty"

    # Year
    year_m = _YEAR_RE.search(query)
    if year_m:
        signals.year_hint = int(year_m.group())

    # Month
    month_m = _MONTH_RE.search(query)
    if month_m:
        raw = month_m.group().lower()
        signals.month_hint = _ABBR_TO_FULL.get(raw, raw.capitalize())

    # Countries
    for country in _COUNTRIES:
        if country in q_lower:
            signals.country_hints.append(country.title())

    # Category
    for pattern, category in _DOMAIN_CATEGORY:
        if pattern.search(query):
            signals.category_hint = category
            break

    # Acronyms
    words = re.findall(r'\b[a-z]+\b', q_lower)
    signals.acronyms_found = [w.upper() for w in words if w in ACRONYM_MAP]

    # Phrase matches (for debug/trace)
    for pattern, expansion in PHRASE_EXPANSIONS:
        if pattern.search(query):
            signals.phrase_matches.append(pattern.pattern[:40])

    logger.debug(
        "Intent: sort=%s free=%s year=%s month=%s cat=%s acronyms=%s phrases=%d",
        signals.sort_hint, signals.free_hint, signals.year_hint, signals.month_hint,
        signals.category_hint, signals.acronyms_found, len(signals.phrase_matches),
        extra={"query": query[:60]},
    )
    return signals


def expand_query(query: str, signals: IntentSignals | None = None) -> str:
    """
    Enrich the query through 6 layers for BM25/hybrid retrieval.

    Layer 1 — Noise stripping:   remove "best", "top", "list of", etc.
    Layer 2 — Phrase expansions: "MBBS entrance" → "NEET AIIMS medical India"
    Layer 3 — Synonym injection: "doctor" → "medical NEET AIIMS MBBS"
    Layer 4 — Acronym expansion: "GRE" → "GRE Graduate Record Examination"
    Layer 5 — Region+domain:     "India engineering" → "JEE GATE IIT NIT"
    Layer 6 — Category append:   append inferred category string

    All layers are additive — original query terms are preserved.
    Duplicate tokens are deduplicated to avoid BM25 score inflation.
    """
    if signals is None:
        signals = extract_intent(query)

    # Track tokens already in the expansion to avoid repetition
    def _tokens(s: str) -> set[str]:
        return set(re.findall(r'[a-z0-9]+', s.lower()))

    # ── Layer 1: strip noise ─────────────────────────────────────────────
    expanded = _NOISE.sub("", query).strip()
    expanded = re.sub(r'\s+', ' ', expanded)

    additions: list[str] = []
    seen = _tokens(expanded)

    def _add(text: str) -> None:
        new_tokens = [t for t in text.split() if t.lower() not in seen]
        if new_tokens:
            additions.append(text)
            seen.update(t.lower() for t in new_tokens)

    # ── Layer 2: phrase expansions ───────────────────────────────────────
    for pattern, expansion in PHRASE_EXPANSIONS:
        if pattern.search(query):
            _add(expansion)

    # ── Layer 3: synonym injections ──────────────────────────────────────
    query_words = set(re.findall(r'\b[a-z]+\b', query.lower()))
    for word, expansion in SYNONYM_INJECTIONS.items():
        if word in query_words:
            _add(expansion)

    # ── Layer 4: acronym expansions ──────────────────────────────────────
    for acronym in signals.acronyms_found:
        key = acronym.lower()
        if key in ACRONYM_MAP:
            _add(ACRONYM_MAP[key])

    # ── Layer 5: region + domain combos ─────────────────────────────────
    for region_pat, domain_pat, injection in REGION_DOMAIN_COMBOS:
        if region_pat.search(query) and domain_pat.search(query):
            _add(injection)

    # ── Layer 6: category hint ───────────────────────────────────────────
    if signals.category_hint:
        _add(signals.category_hint)

    # Assemble
    if additions:
        expanded = expanded + " " + " ".join(additions)
    expanded = re.sub(r'\s+', ' ', expanded).strip()

    ratio = len(expanded) / max(len(query), 1)
    logger.debug(
        "Query expanded (%.1fx): '%s' → '%s'",
        ratio, query[:50], expanded[:120],
        extra={"query": query[:60]},
    )
    return expanded
