from classifurlr.classification import Classifier

class EmptyPageClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Empty page'
        self.desc = 'A classifier that says pages with very little content are down'
        self.size_cutoff = 300 # bytes

    def page_down_confidence(self, page, session):
        total_size = page.get_total_size(page.entries)
        return 1.0 if total_size <= self.size_cutoff else 0.0

