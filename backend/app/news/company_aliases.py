from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata

from app.market_data.symbols import BIST100_SYMBOLS

AUTO_LINK_THRESHOLD = 85
LEGAL_SUFFIXES = {"anonim","sirketi","sirket","a","s","as","ao","ta","tao","holding","sanayi","ticaret","ve","co","company","inc","ltd","limited"}

@dataclass(frozen=True)
class CompanyIdentity:
    ticker: str
    official_name: str
    short_name: str
    aliases: tuple[str, ...] = ()
    brand_names: tuple[str, ...] = ()

    @property
    def all_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.official_name,self.short_name,*self.aliases,*self.brand_names)))

@dataclass(frozen=True)
class CompanyMatch:
    symbol: str
    confidence: int
    match_method: str
    detected_company: str
    position: int = 0

@dataclass(frozen=True)
class CompanyResolution:
    links: tuple[CompanyMatch, ...]
    primary_symbol: str | None
    unmatched_reason: str | None
    best_candidate: str | None = None
    best_confidence: int | None = None

# official_name, short_name, aliases/brands. This table intentionally covers the exact scanner universe.
_ROWS = {
"AEFES":("Anadolu Efes Biracılık ve Malt Sanayii A.Ş.","Anadolu Efes",("Efes",)),
"AGHOL":("AG Anadolu Grubu Holding A.Ş.","Anadolu Grubu",("Anadolu Holding",)),
"AHGAZ":("Ahlatcı Doğal Gaz Dağıtım Enerji ve Yatırım A.Ş.","Ahlatcı Doğal Gaz",("Ahlatcı Gaz",)),
"AKBNK":("Akbank T.A.Ş.","Akbank",()),"AKCNS":("Akçansa Çimento Sanayi ve Ticaret A.Ş.","Akçansa",()),
"AKFGY":("Akfen Gayrimenkul Yatırım Ortaklığı A.Ş.","Akfen GYO",()),"AKFYE":("Akfen Yenilenebilir Enerji A.Ş.","Akfen Yenilenebilir",()),
"AKSA":("Aksa Akrilik Kimya Sanayii A.Ş.","Aksa Akrilik",()),"AKSEN":("Aksa Enerji Üretim A.Ş.","Aksa Enerji",()),
"ALARK":("Alarko Holding A.Ş.","Alarko",()),"ALBRK":("Albaraka Türk Katılım Bankası A.Ş.","Albaraka Türk",("Albaraka",)),
"ALFAS":("Alfa Solar Enerji Sanayi ve Ticaret A.Ş.","Alfa Solar",()),"ARCLK":("Arçelik A.Ş.","Arçelik",("Beko",)),
"ARDYZ":("Ard Grup Bilişim Teknolojileri A.Ş.","ARD Bilişim",("ARD Grup",)),"ASELS":("Aselsan Elektronik Sanayi ve Ticaret A.Ş.","ASELSAN",()),
"ASTOR":("Astor Enerji A.Ş.","Astor Enerji",("Astor",)),"BERA":("Bera Holding A.Ş.","Bera Holding",()),
"BIMAS":("BİM Birleşik Mağazalar A.Ş.","BİM",("Bim Birleşik Mağazalar",)),"BIOEN":("Biotrend Çevre ve Enerji Yatırımları A.Ş.","Biotrend",()),
"BRSAN":("Borusan Birleşik Boru Fabrikaları Sanayi ve Ticaret A.Ş.","Borusan Boru",("Borusan Mannesmann",)),
"BRYAT":("Borusan Yatırım ve Pazarlama A.Ş.","Borusan Yatırım",()),"BUCIM":("Bursa Çimento Fabrikası A.Ş.","Bursa Çimento",()),
"CANTE":("Çan2 Termik A.Ş.","Çan2 Termik",("Can2 Termik",)),"CCOLA":("Coca-Cola İçecek A.Ş.","Coca-Cola İçecek",("CCI",)),
"CIMSA":("Çimsa Çimento Sanayi ve Ticaret A.Ş.","Çimsa",()),"CWENE":("CW Enerji Mühendislik Ticaret ve Sanayi A.Ş.","CW Enerji",()),
"DOAS":("Doğuş Otomotiv Servis ve Ticaret A.Ş.","Doğuş Otomotiv",()),"DOHOL":("Doğan Şirketler Grubu Holding A.Ş.","Doğan Holding",("Doğan Grubu",)),
"ECILC":("EİS Eczacıbaşı İlaç Sınai ve Finansal Yatırımlar A.Ş.","Eczacıbaşı İlaç",()),"ECZYT":("Eczacıbaşı Yatırım Holding Ortaklığı A.Ş.","Eczacıbaşı Yatırım",()),
"EGEEN":("Ege Endüstri ve Ticaret A.Ş.","Ege Endüstri",()),"EKGYO":("Emlak Konut Gayrimenkul Yatırım Ortaklığı A.Ş.","Emlak Konut",("Emlak Konut GYO",)),
"ENERY":("Enerya Enerji A.Ş.","Enerya Enerji",("Enerya",)),"ENJSA":("Enerjisa Enerji A.Ş.","Enerjisa",()),
"ENKAI":("Enka İnşaat ve Sanayi A.Ş.","ENKA İnşaat",("ENKA",)),"EREGL":("Ereğli Demir ve Çelik Fabrikaları T.A.Ş.","Erdemir",("Ereğli Demir Çelik",)),
"EUPWR":("Europower Enerji ve Otomasyon Teknolojileri Sanayi Ticaret A.Ş.","Europower Enerji",("Europower",)),
"EUREN":("Europen Endüstri İnşaat Sanayi ve Ticaret A.Ş.","Europen",()),"FROTO":("Ford Otomotiv Sanayi A.Ş.","Ford Otosan",()),
"GARAN":("Türkiye Garanti Bankası A.Ş.","Garanti BBVA",("Garanti Bankası",)),"GESAN":("Girişim Elektrik Sanayi Taahhüt ve Ticaret A.Ş.","Girişim Elektrik",()),
"GLYHO":("Global Yatırım Holding A.Ş.","Global Yatırım Holding",()),"GUBRF":("Gübre Fabrikaları T.A.Ş.","Gübretaş",("Gübre Fabrikaları",)),
"HALKB":("Türkiye Halk Bankası A.Ş.","Halkbank",("Halk Bankası",)),"HEKTS":("Hektaş Ticaret T.A.Ş.","Hektaş",()),
"IPEKE":("İpek Doğal Enerji Kaynakları Araştırma ve Üretim A.Ş.","İpek Doğal Enerji",()),"ISCTR":("Türkiye İş Bankası A.Ş.","İş Bankası",("Türkiye İş Bankası",)),
"ISDMR":("İskenderun Demir ve Çelik A.Ş.","İsdemir",("İskenderun Demir Çelik",)),"ISMEN":("İş Yatırım Menkul Değerler A.Ş.","İş Yatırım",()),
"KARSN":("Karsan Otomotiv Sanayii ve Ticaret A.Ş.","Karsan",()),"KCAER":("Kocaer Çelik Sanayi ve Ticaret A.Ş.","Kocaer Çelik",()),
"KCHOL":("Koç Holding A.Ş.","Koç Holding",("Koç Grubu",)),"KLSER":("Kaleseramik Çanakkale Kalebodur Seramik Sanayi A.Ş.","Kaleseramik",("Kalebodur",)),
"KONTR":("Kontrolmatik Teknoloji Enerji ve Mühendislik A.Ş.","Kontrolmatik",()),"KONYA":("Konya Çimento Sanayii A.Ş.","Konya Çimento",()),
"KOZAA":("Koza Anadolu Metal Madencilik İşletmeleri A.Ş.","Koza Anadolu Metal",()),"KOZAL":("Koza Altın İşletmeleri A.Ş.","Koza Altın",()),
"KRDMD":("Kardemir Karabük Demir Çelik Sanayi ve Ticaret A.Ş.","Kardemir",()),"MAVI":("Mavi Giyim Sanayi ve Ticaret A.Ş.","Mavi",("Mavi Jeans",)),
"MGROS":("Migros Ticaret A.Ş.","Migros",()),"MIATK":("Mia Teknoloji A.Ş.","Mia Teknoloji",()),
"ODAS":("Odaş Elektrik Üretim Sanayi Ticaret A.Ş.","Odaş Elektrik",("Odaş",)),"OTKAR":("Otokar Otomotiv ve Savunma Sanayi A.Ş.","Otokar",()),
"OYAKC":("Oyak Çimento Fabrikaları A.Ş.","Oyak Çimento",()),"PENTA":("Penta Teknoloji Ürünleri Dağıtım Ticaret A.Ş.","Penta Teknoloji",()),
"PETKM":("Petkim Petrokimya Holding A.Ş.","Petkim",()),"PGSUS":("Pegasus Hava Taşımacılığı A.Ş.","Pegasus",("Pegasus Hava Yolları",)),
"QUAGR":("Qua Granite Hayal Yapı ve Ürünleri Sanayi Ticaret A.Ş.","Qua Granite",()),"SAHOL":("Hacı Ömer Sabancı Holding A.Ş.","Sabancı Holding",("Sabancı Grubu",)),
"SASA":("Sasa Polyester Sanayi A.Ş.","Sasa Polyester",("SASA",)),"SDTTR":("SDT Uzay ve Savunma Teknolojileri A.Ş.","SDT Uzay ve Savunma",("SDT",)),
"SISE":("Türkiye Şişe ve Cam Fabrikaları A.Ş.","Şişecam",("Şişe Cam",)),"SKBNK":("Şekerbank T.A.Ş.","Şekerbank",()),
"SMRTG":("Smart Güneş Enerjisi Teknolojileri Araştırma Geliştirme Üretim A.Ş.","Smart Güneş",()),"SOKM":("Şok Marketler Ticaret A.Ş.","Şok Marketler",("Şok",)),
"TABGD":("Tab Gıda Sanayi ve Ticaret A.Ş.","TAB Gıda",("Burger King Türkiye",)),"TAVHL":("TAV Havalimanları Holding A.Ş.","TAV Havalimanları",("TAV Airports",)),
"TCELL":("Turkcell İletişim Hizmetleri A.Ş.","Turkcell",()),"THYAO":("Türk Hava Yolları A.O.","Türk Hava Yolları",("Turkish Airlines","THY")),
"TKFEN":("Tekfen Holding A.Ş.","Tekfen",()),"TOASO":("Tofaş Türk Otomobil Fabrikası A.Ş.","Tofaş",()),
"TSKB":("Türkiye Sınai Kalkınma Bankası A.Ş.","TSKB",()),"TTKOM":("Türk Telekomünikasyon A.Ş.","Türk Telekom",()),
"TTRAK":("Türk Traktör ve Ziraat Makineleri A.Ş.","TürkTraktör",("Türk Traktör",)),"TUKAS":("Tukaş Gıda Sanayi ve Ticaret A.Ş.","Tukaş",()),
"TUPRS":("Türkiye Petrol Rafinerileri A.Ş.","Tüpraş",()),"ULKER":("Ülker Bisküvi Sanayi A.Ş.","Ülker",("Ülker Bisküvi",)),
"VAKBN":("Türkiye Vakıflar Bankası T.A.O.","VakıfBank",("Vakıflar Bankası",)),"VESBE":("Vestel Beyaz Eşya Sanayi ve Ticaret A.Ş.","Vestel Beyaz Eşya",()),
"VESTL":("Vestel Elektronik Sanayi ve Ticaret A.Ş.","Vestel",()),"YEOTK":("Yeo Teknoloji Enerji ve Endüstri A.Ş.","YEO Teknoloji",("YEO",)),
"YKBNK":("Yapı ve Kredi Bankası A.Ş.","Yapı Kredi",("Yapı Kredi Bankası",)),"YYLGD":("Yayla Agro Gıda Sanayi ve Ticaret A.Ş.","Yayla Agro",()),
"ZOREN":("Zorlu Enerji Elektrik Üretim A.Ş.","Zorlu Enerji",()),"AKENR":("Akenerji Elektrik Üretim A.Ş.","Akenerji",()),
"ANSGR":("Anadolu Anonim Türk Sigorta Şirketi","Anadolu Sigorta",()),"BAGFS":("Bagfaş Bandırma Gübre Fabrikaları A.Ş.","Bagfaş",()),
"BIZIM":("Bizim Toptan Satış Mağazaları A.Ş.","Bizim Toptan",()),"DEVA":("Deva Holding A.Ş.","Deva Holding",("Deva İlaç",)),
"GENIL":("Gen İlaç ve Sağlık Ürünleri Sanayi ve Ticaret A.Ş.","Gen İlaç",()),
}
COMPANY_MASTER={ticker:CompanyIdentity(ticker,*values) for ticker,values in _ROWS.items()}
assert set(COMPANY_MASTER)==set(BIST100_SYMBOLS),"Company master must cover the scanner universe"

def normalize_company_text(value:str,*,strip_legal_suffixes:bool=False)->str:
    value=unicodedata.normalize("NFKD",value.casefold().replace("ı","i"))
    tokens=re.findall(r"[a-z0-9]+","".join(ch for ch in value if not unicodedata.combining(ch)))
    if strip_legal_suffixes:tokens=[x for x in tokens if x not in LEGAL_SUFFIXES]
    return " ".join(tokens)

def _position(haystack:str,needle:str)->int|None:
    found=re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",haystack)
    return found.start() if found else None

def _boilerplate_context(text:str,position:int|None,name:str)->bool:
    if position is None:return False
    window=text[max(0,position-30):position+len(name)+60]
    return any(term in window for term in ("katkilariyla","sponsorlugunda","sponsorlu","reklam","advertorial"))

def resolve_company_symbols(*texts:str,structured_symbol:str|None=None,structured_company:str|None=None)->CompanyResolution:
    original=" ".join(x for x in ((structured_company or ""),*texts) if x)
    normalized=normalize_company_text(original);raw=original.casefold();matches={};candidates=[]
    def consider(item):
        old=matches.get(item.symbol)
        if old is None or (-item.confidence,item.position,item.symbol)<(-old.confidence,old.position,old.symbol):matches[item.symbol]=item
    if structured_symbol and structured_symbol.upper() in COMPANY_MASTER:
        ticker=structured_symbol.upper();consider(CompanyMatch(ticker,100,"TICKER_EXACT",structured_company or ticker,-1))
    for ticker,identity in COMPANY_MASTER.items():
        hit=re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])",original)
        if hit:consider(CompanyMatch(ticker,100,"TICKER_EXACT",ticker,hit.start()));continue
        for index,name in enumerate(identity.all_names):
            folded=name.casefold();needle=normalize_company_text(name,strip_legal_suffixes=index==0)
            if not needle:continue
            raw_pos=_position(raw,folded);pos=_position(normalized,needle)
            if _boilerplate_context(normalized,pos,needle):continue
            short_guard=len(needle)<=3 and not re.search(rf"(?<!\w){re.escape(name.upper())}(?!\w)",original)
            if index==0 and pos is not None and not short_guard:consider(CompanyMatch(ticker,100,"OFFICIAL_NAME",name,pos))
            elif raw_pos is not None and not short_guard:consider(CompanyMatch(ticker,95,"ALIAS_EXACT",name,raw_pos))
            elif pos is not None and not short_guard:consider(CompanyMatch(ticker,90,"NORMALIZED_ALIAS",name,pos))
            else:
                tokens=[x for x in needle.split() if len(x)>=4];present=[x for x in tokens if _position(normalized,x) is not None]
                if len(tokens)>=2 and len(present)>=2 and len(present)/len(tokens)>=.6:
                    candidates.append(CompanyMatch(ticker,80,"TOKEN_MATCH",name,normalized.find(present[0])))
                elif len(needle)>=7 and SequenceMatcher(None,needle,normalized[:len(needle)]).ratio()>=.86:
                    candidates.append(CompanyMatch(ticker,60,"FUZZY",name,0))
    strong=sorted(matches.values(),key=lambda x:(-x.confidence,x.position,x.symbol))
    # Prefer a more specific company phrase over a nested brand at the same location.
    specific=[]
    for item in strong:
        name=normalize_company_text(item.detected_company)
        shadowed=any(item.position==other.position and item.confidence<other.confidence and
            name!=normalize_company_text(other.detected_company) and
            name in normalize_company_text(other.detected_company) for other in strong)
        if not shadowed:specific.append(item)
    strong=specific
    if len(strong)>1 and strong[0].position==strong[1].position and strong[0].confidence==strong[1].confidence:
        return CompanyResolution((),None,"AMBIGUOUS_MATCH",strong[0].symbol,strong[0].confidence)
    if strong:
        return CompanyResolution(tuple(strong),strong[0].symbol,None,strong[0].symbol,strong[0].confidence)
    if candidates:
        best=sorted(candidates,key=lambda x:(-x.confidence,x.position,x.symbol))[0]
        return CompanyResolution((),None,"LOW_CONFIDENCE",best.symbol,best.confidence)
    reason="EMPTY_CONTENT" if not normalized else "NO_COMPANY_SIGNAL" if len(normalized.split())<2 else "NO_COMPANY_MATCH"
    return CompanyResolution((),None,reason)

def map_company_symbol(*texts:str)->tuple[str|None,str|None]:
    result=resolve_company_symbols(*texts)
    primary=next((x for x in result.links if x.symbol==result.primary_symbol),None)
    return result.primary_symbol,primary.detected_company if primary else None
