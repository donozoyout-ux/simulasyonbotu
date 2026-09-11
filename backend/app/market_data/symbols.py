# Configurable V1 universe. Index membership changes over time; keep this seed list
# separate from the provider so it can later be replaced by a maintained symbol source.
BIST100_SYMBOLS = [
    "AEFES","AGHOL","AHGAZ","AKBNK","AKCNS","AKFGY","AKFYE","AKSA","AKSEN","ALARK",
    "ALBRK","ALFAS","ARCLK","ARDYZ","ASELS","ASTOR","BERA","BIMAS","BIOEN","BRSAN",
    "BRYAT","BUCIM","CANTE","CCOLA","CIMSA","CWENE","DOAS","DOHOL","ECILC","ECZYT",
    "EGEEN","EKGYO","ENERY","ENJSA","ENKAI","EREGL","EUPWR","EUREN","FROTO","GARAN",
    "GESAN","GLYHO","GUBRF","HALKB","HEKTS","IPEKE","ISCTR","ISDMR","ISMEN","KARSN",
    "KCAER","KCHOL","KLSER","KONTR","KONYA","KOZAA","KOZAL","KRDMD","MAVI","MGROS",
    "MIATK","ODAS","OTKAR","OYAKC","PENTA","PETKM","PGSUS","QUAGR","SAHOL","SASA",
    "SDTTR","SISE","SKBNK","SMRTG","SOKM","TABGD","TAVHL","TCELL","THYAO","TKFEN",
    "TOASO","TSKB","TTKOM","TTRAK","TUKAS","TUPRS","ULKER","VAKBN","VESBE","VESTL",
    "YEOTK","YKBNK","YYLGD","ZOREN","AKENR","ANSGR","BAGFS","BIZIM","DEVA","GENIL"
]

# Twelve Data's stock discovery identifies the exchange but does not expose
# BIST index membership. Keep a conservative, auditable large-cap exclusion
# snapshot separate from provider code. It may intentionally exclude a few
# borderline names rather than leak BIST30 exposure into the small/mid test.
BIST30_EXCLUSION_SYMBOLS = {
    "AEFES", "AKBNK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "EKGYO",
    "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "ISCTR", "KCHOL", "KOZAL",
    "KRDMD", "MGROS", "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TAVHL",
    "TCELL", "THYAO", "TOASO", "TTKOM", "TUPRS", "ULKER", "YKBNK",
}
