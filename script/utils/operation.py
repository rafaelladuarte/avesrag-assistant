import unicodedata


def normalize_text(text):
    if not text:
        return ''
    text = text.lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join([c for c in text if not unicodedata.combining(c)])
    return text


def map_color(color):
    color_mapping = {
        "branco": "bran",
        "preto": "pret",
        "marrom": "marr",
        "amarelo": "amarel",
        "verde": "verd",
        "vermelho": "verme",
        "cinza": "cinz",
        "laranja": "laranj"
    }
    return color_mapping.get(color, color)
