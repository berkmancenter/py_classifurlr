class BlockedFinder():
    def __init__(self):
        self.name = 'Blocked Finder'
        self.desc = 'Determines whether a session is blocked by looking for blocked pages'

    def process(self, session_classification):
        if not session_classification.is_down(): return session_classification
        for pc in session_classification.constituents:
            for pcc in pc.constituents:
                if pcc.is_blocked() or pc.is_blocked():
                    pc.mark_blocked()
                    session_classification.mark_blocked()

        return session_classification

