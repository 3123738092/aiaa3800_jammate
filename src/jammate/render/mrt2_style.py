"""Build and validate short style text at the MRT2 boundary."""
import re
from unicodedata import normalize

STYLE_LIMIT = 240

_TERMS = (
    (r"完整(?:的)?乡村(?:音乐)?伴奏", "full country accompaniment"),
    (r"巴萨诺瓦(?:风格的音乐|风格|音乐)?|波萨诺瓦", "bossa nova"),
    (r"硬摇滚(?:风格的音乐|风格|音乐)?", "hard rock"),
    (r"乡村(?:音乐)?", "country music"),
    (r"(?:以)?尼龙弦吉他(?:为主)?", "nylon guitar"),
    (r"带混响的\s*shoegaze\s*吉他", "reverberant shoegaze guitar"),
    (r"失真(?:电)?吉他(?:\s*riffs?|连复段)?", "distorted electric guitar riffs"),
    (r"电吉他(?:\s*riffs?|连复段)", "electric guitar riff"),
    (r"电吉他", "electric guitar"),
    (r"木吉他", "acoustic guitar"),
    (r"吉他", "guitar"),
    (r"迷幻(?:的)?合成器(?:音色)?", "psychedelic synthesizer"),
    (r"梦幻(?:的)?氛围(?:音色)?", "dreamy ambient textures"),
    (r"萨克斯(?:风)?独奏", "solo saxophone"),
    (r"萨克斯(?:风)?", "saxophone"),
    (r"钢琴旋律", "piano melody"),
    (r"键盘(?:音色)?", "keyboards"),
    (r"钢琴", "piano"),
    (r"轻柔(?:的)?打击乐|打击乐轻一点", "light percussion"),
    (r"富有推进感的鼓点|鼓点有推进感", "driving drums"),
    (r"鼓点|鼓组|(?<![\w])鼓(?![\w])", "drum groove"),
    (r"完整(?:的)?伴奏", "full accompaniment"),
)
_PREFIX = re.compile(
    r"^(?:(?:请)?(?:只)?(?:给我(?:来)?(?:一段|个)?|来点|来一段|来一些|我想要|用|加入(?:一点)?|只要)|"
    r"(?:please )?give me (?:some|a)\s+)", re.I)
_TIMING = re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*bpm\b|(?<!\d)4\s*/\s*4\s*(?:拍|time)?", re.I)
_KEY = re.compile(r"(?<![a-z])[a-g](?:[#♯b♭])?\s*(?:major\b|minor\b|大调|小调)", re.I)


def english_style(text):
    """Validate final production style text before calling MusicCoCa."""
    if not isinstance(text, str):
        raise ValueError("MRT2 style must be a short English music description")
    text = text.translate(str.maketrans({"♯": "#", "♭": "b", "–": "-", "—": "-", "’": "'", "“": '"', "”": '"'}))
    text = "".join(c for c in normalize("NFKD", text) if not 0x300 <= ord(c) <= 0x36f)
    text = " ".join(text.split())
    if not (0 < len(text) <= STYLE_LIMIT and text.isascii() and re.search(r"[a-z]", text, re.I)):
        raise ValueError("MRT2 需要简短的英文音乐描述。请检查风格转换或输入英文。")
    return text


def local_style(text):
    """Translate common musical terms. Leave unknown requests for the interpreter."""
    text = normalize("NFKC", text).strip()
    text = _KEY.sub("", _TIMING.sub("", text))
    parts = []
    for clause in re.split(r"[,，。;；]", text):
        clause = _PREFIX.sub("", clause.strip()).strip()
        if re.fullmatch(r"(?:每小节)?(?:在)?第[\d.、和与\s]+拍(?:起音)?|"
                        r"(?:把)?和弦(?:进行|走向)?\s*(?:设为|设置为|改成|改为|换成|换为)\s*[a-g].*",
                        clause, re.I):
            continue
        for pattern, term in _TERMS:
            clause = re.sub(pattern, term, clause, flags=re.I)
        clause = re.sub(r"(?:演奏|为主)$", "", clause).strip(" .。")
        if clause:
            parts.append(clause)
    return ", ".join(parts)
