import base64

from bs4 import BeautifulSoup
from cachetools import cached, LRUCache

from .classification import NotEnoughDataError

def entry_to_key(entry):
    if entry:
        return (entry['request']['url'], entry['startedDateTime'], entry['pageref'])

@cached(cache=LRUCache(maxsize=32), key=entry_to_key)
def har_entry_response_content(entry):
    try:
        content = entry['response']['content']
    except Exception:
        raise NotEnoughDataError('Could not parse entry content')
    if 'text' not in content:
        raise NotEnoughDataError('"text" field not found in entry content')
    text = content['text']
    if 'encoding' in content and content['encoding'] == 'base64':
        text = base64.b64decode(text)
    # BeautifulSoup takes care of the document encoding for us.
    try:
        return str(BeautifulSoup(text, 'lxml'))
    except Exception as e:
        raise NotEnoughDataError('Could not parse entry content')

def get_total_size(entries):
  size = 0
  for entry in entries:
    if entry['response']['content']['size'] > 0:
      size += entry['response']['content']['size']
  return size
