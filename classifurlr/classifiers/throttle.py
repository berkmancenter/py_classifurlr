import logging

from classifurlr.classification import Classifier

class ThrottleClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Throttle'
        self.desc = 'Detects excessively long load times that might indicate throttling'
        self.total_confidence_above_size = 600
        self.time_threshold = 5 * 60 * 1000 # 5 minutes
        self.bandwidth_threshold = 50 # kbps

    def page_down_confidence(self, page, session):
        bites = page.get_total_size(page.entries)
        kilobits = bites * 8 / 1000.0
        mss = page.get_load_time()
        seconds = mss / 1000.0
        kilobits_per_sec = kilobits/seconds
        logging.debug("{} - Page: {} - Bytes: {} - Time (ms): {} - kbps: {}".format(
            self.slug(), page.page_id, bites, mss, round(kilobits_per_sec, 3)))
        down = (mss >= self.time_threshold
                and kilobits_per_sec <= self.bandwidth_threshold)
        confidence = 0.0
        if down:
            # Simple linear intepolation between 0 and 100% confidence
            confidence = min(1.0, bites / self.total_confidence_above_size)
        return confidence

