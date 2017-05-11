import logging

from classifurlr.classification import Classifier, NotEnoughDataError

class StatusCodeClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Status code'
        self.desc = 'A simple classifier that says all non-2xx status codes are down'

    def page_down_confidence(self, page, session):
        entry = page.actual_page
        if entry is None:
            raise NotEnoughDataError('No final page found')
        if 'response' not in entry or 'status' not in entry['response']:
            raise NotEnoughDataError('"response" or "status" not found in entry '
                    'for URL "{}"'.format(entry['rekwest']['url']))
        status = page.actual_page['response']['status']
        logging.debug("{} - Page: {} - Status: {}".format(self.slug(), page.page_id,
            status))
        return 1.0 if status < 200 or status > 299 else 0.0

