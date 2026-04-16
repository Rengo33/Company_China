"""
Shared skip list for domains that are NOT real company websites.

Split into categories so we can extend each independently.
"""

# B2B directories / aggregator sites that show up in search results
B2B_DIRECTORIES = {
    "qcc.com", "tianyancha.com", "qichacha.com",
    "business-yellowpages.com", "yellowpages.com",
    "rocketreach.co", "zoominfo.com", "dnb.com", "crunchbase.com",
    "tradechina.com", "importgenius.com", "chemicalbook.com",
    "101.com", "goldsupplier.com", "topchinasupplier.com",
    "gotosuppliers.com", "tradewheel.com", "company-listing.org",
    "cautop.com", "en.ec21.com", "ec21.com",
    "globalsources.com", "diytrade.com", "exportbureau.com",
    "trademarks.justia.com", "trademarkia.com", "patsnap.com",
    "synapse.patsnap.com",
    "fiata.org",  # industry body
    "accio.com", "work-download.accio.com",  # alibaba ai tool
    "jctrans.com", "m.jctrans.com",
    "chinaaseantrade.com", "jinhanfair.com", "i.jinhanfair.com",
    "yiwugo.com", "en.yiwugo.com",
    "52wmb.com", "en.52wmb.com",
    "alibabagroup.com", "1688.com",
    "baba-blog.com",  # alibaba blog
}

# Hosting, DNS, WHOIS privacy providers
HOSTING_WHOIS = {
    "xinnet.com", "cnnic.cn", "300.cn", "dm-longgang.com",
    "scaleway.com", "cloudflare.com", "godaddy.com",
    "namecheap.com", "hichina.com", "net.cn",
    "aliyun.com", "aliyuncs.com",
    "whoisguard.com", "domainsbyproxy.com", "whoisprivacyservice.org",
    "privacyprotect.org", "contactprivacy.com",
    "dnspod.cn", "dnspod.com",
}

# Social media and big tech
BIG_TECH_SOCIAL = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "tiktok.com",
    "pinterest.com", "reddit.com",
    "wikipedia.org", "bloomberg.com", "reuters.com",
    "bing.com", "google.com", "duckduckgo.com", "yandex.com",
    "apple.com", "microsoft.com",
    "weibo.com", "xiaohongshu.com", "zhihu.com",
    "baidu.com", "sohu.com", "sina.com.cn",
    "wechat.com", "weixin.qq.com", "qq.com",
}

# E-commerce marketplaces (not the seller's own site)
MARKETPLACES = {
    "amazon.com", "amazon.de", "amazon.co.uk", "amazon.fr",
    "amazon.it", "amazon.es", "amazon.nl", "amazon.pl",
    "ebay.com", "ebay.de", "ebay.co.uk",
    "alibaba.com", "aliexpress.com", "made-in-china.com",
    "temu.com", "shein.com", "wish.com",
    "etsy.com", "walmart.com",
}

# News and content aggregators
CONTENT = {
    "chip.de",  # German tech review site
    "newskk.cc", "cnxmneostarmade.newskk.cc",
    "madeinchina.com",  # news aggregator
    "foshan.furniture",  # fake TLD directory
}

# Combined skip set
SKIP_DOMAINS = (
    B2B_DIRECTORIES
    | HOSTING_WHOIS
    | BIG_TECH_SOCIAL
    | MARKETPLACES
    | CONTENT
)


def is_skip_domain(domain: str) -> bool:
    """
    Check if a domain matches a skip entry.
    Uses proper domain matching (endswith), not substring.
    """
    if not domain:
        return True

    domain = domain.lower().strip()

    # Strip leading www.
    if domain.startswith("www."):
        domain = domain[4:]

    # Check exact match or subdomain match
    for skip in SKIP_DOMAINS:
        if domain == skip or domain.endswith("." + skip):
            return True

    return False


def is_skip_email(email: str) -> bool:
    """Check if an email is from a skip domain or is a junk/generic address."""
    if not email or "@" not in email:
        return True

    local, domain = email.lower().rsplit("@", 1)

    if is_skip_domain(domain):
        return True

    # Junk local parts
    junk_prefixes = {
        "noreply", "no-reply", "mailer-daemon", "postmaster",
        "webmaster", "abuse", "sentry", "privacy", "legal",
        "dmca", "supervision", "complaint", "admin@example",
    }
    for prefix in junk_prefixes:
        if local.startswith(prefix):
            return True

    # Free email providers are not company emails
    free_email = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "163.com", "126.com", "qq.com", "sina.com", "sohu.com",
        "foxmail.com", "yeah.net", "aliyun.com",
    }
    if domain in free_email:
        return True

    return False
