import logging

from ..classification import ClassifierWithBaseline, NotEnoughDataError

class PageLengthClassifier(ClassifierWithBaseline):
    def __init__(self):
        super().__init__()
        self.name = 'Page length'
        self.desc = 'Detects whether a page is a block page by page length given a baseline'
        self.page_length_threshold = 0.3019

    def response_len(self, entry):
        try:
            return entry['response']['content']['size']
        except KeyError as e:
            raise NotEnoughDataError('Could not determine response '
                    'size for URL "{}"'.format(entry['request']['url'])) from e

    def page_down_confidence(self, page, session):
        baseline = self.get_baseline(session)
        baseline_len = self.response_len(baseline.actual_page)
        this_content_len = self.response_len(page.actual_page)
        length_ratio = abs(baseline_len - this_content_len) / max(baseline_len, this_content_len)
        logging.debug("{} - Page: {} - Baseline: {} - Content: {} - Diff Ratio: {}".format(
            self.slug(), page.page_id, baseline_len, this_content_len, round(length_ratio, 3)))
        return 1.0 if length_ratio >= self.page_length_threshold else 0.0

