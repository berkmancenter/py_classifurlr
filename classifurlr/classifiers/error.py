import logging

from classifurlr.classification import Classifier, NotEnoughDataError

class ErrorClassifier(Classifier):
    def __init__(self):
        Classifier.__init__(self)
        self.name = 'Error'
        self.desc = 'Classifies all session that contain errors as down'

    def is_blocked_in_china(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'CN':
            return None # None means we don't know if it's blocked or not
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or [] in errors or len(errors) <= 1:
            return None
        errors = [e[0] for e in errors]
        if all(['Operation canceled' in e for e in errors]):
            return True
        return None

    def is_blocked_in_kazakhstan(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'KZ':
            return None # None means we don't know if it's blocked or not
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or [] in errors or len(errors) <= 1:
            return None
        errors = [e[0] for e in errors]
        if all([('Operation canceled' in e) for e in errors]):
            return True
        return None

    def is_blocked_in_lebanon(self, page, session, classification):
        country = session.get_page_country_code(page.page_id)
        if country is None or country != 'LB':
            return None
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # All pages must have the error, and there must be more than one page.
        if errors is None or None in errors or [] in errors or len(errors) <= 1:
            return None
        errors = [e[0] for e in errors]
        if all([('Connection closed' in e) for e in errors]):
            return True
        return None

    def is_blocked_in_turkey(self, page, session, classification):
        asn = session.get_page_asn(page.page_id)
        country = session.get_page_country_code(page.page_id)
        if not (country == 'TR' and asn == 197328):
            return None
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # More than one page must have the error. We have enough data for this.
        if errors is None or len(errors) <= 1:
            return None
        errors = [e[0] for e in errors if e[0] == "(56, 'Recv failure: Connection reset by peer')"]
        if len(errors) > 1:
            return True
        return None

    def is_blocked_in_iran(self, page, session, classification):
        asn = session.get_page_asn(page.page_id)
        country = session.get_page_country_code(page.page_id)
        if not (country == 'IR' and asn == 48434):
            return None
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # Only need to see this error once. Not great, but we don't have enough data otherwise.
        if errors is None or len(errors) == 0:
            return None
        errors = [e[0] for e in errors if e[0] == "(56, 'Recv failure: Connection reset by peer')"]
        if len(errors) > 0:
            return True
        return None

    def is_blocked_in_indonesia(self, page, session, classification):
        asn = session.get_page_asn(page.page_id)
        country = session.get_page_country_code(page.page_id)
        if not (country == 'ID' and asn in [55699, 23700]):
            return None
        errors = [session.get_page_errors(p.page_id) for p in session.get_pages()]
        # Only need to see this error once. Not great, but we don't have enough data otherwise.
        if errors is None or len(errors) == 0:
            return None
        errors = [e[0] for e in errors if e[0] == "(52, 'Empty reply from server')"]
        if len(errors) > 0:
            return True
        return None

    def is_page_blocked(self, page, session, classification):
        if classification.is_up(): return False
        return (self.is_blocked_in_china(page, session, classification) or
                self.is_blocked_in_lebanon(page, session, classification) or
                self.is_blocked_in_turkey(page, session, classification) or
                self.is_blocked_in_indonesia(page, session, classification) or
                self.is_blocked_in_iran(page, session, classification) or
                self.is_blocked_in_kazakhstan(page, session, classification))

    def page_down_confidence(self, page, session):
        errors = session.get_page_errors(page.page_id)
        if errors is None or len(errors) == 0:
            raise NotEnoughDataError('No errors for page "{}"'.format(page.page_id))
        logging.debug("{} - Page: {} - Errors: {}".format(self.slug(), page.page_id,
            errors))
        return 1.0 if len(errors) > 0 else 0.0

