import tldextract
import urllib.parse, ipaddress

def is_ip(url):
    netloc = urllib.parse.urlparse(url).netloc
    if ':' in netloc:
        netloc = netloc.split(':')[0]
    try:
        ipaddress.ip_address(netloc)
        return True
    except ValueError:
        return False

def extract_domain(url):
    if is_ip(url):
        return urllib.parse.urlparse(url).netloc # IP and port
    return tldextract.extract(url).registered_domain
