import logging

from classifurlr.classification import ClassifierWithBaseline, NotEnoughDataError
from classifurlr.classifiers.similarity_metrics import similarity_metrics

class CosineSimilarityClassifier(ClassifierWithBaseline):
    def __init__(self):
        super().__init__()
        self.name = 'Cosine similarity'
        self.desc = ('Uses cosine similarity between a page and a baseline '
            'to determine whether a page is a block page')
        self.page_length_threshold = 0.3019
        self.cosine_sim_threshold = 0.816
        self.dom_sim_threshold = 0.995

    def page_down_confidence(self, page, session):
        baseline = self.get_baseline(session)
        baseline_content = self.har_entry_response_content(baseline.actual_page)
        try:
            this_content = self.har_entry_response_content(page.actual_page)
        except NotEnoughDataError as e:
            raise NotEnoughDataError('Could not locate page '
                    'content for URL "{}"'.format(page.url)) from e
        metrics = similarity_metrics(baseline_content, this_content)
        logging.debug("{} - Page: {} - Metric: {}".format(self.slug(), page.page_id,
            round(metrics['cosine similarity'], 3)))
        return 1.0 if metrics['cosine similarity'] <= self.cosine_sim_threshold else 0.0

