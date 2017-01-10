import unittest, classifurlr
from classifurlr import *

class PipelineTest(unittest.TestCase):
    def test_tally_vote_equal_up_weights(self):
        fiers = [
                (StatusCodeClassifier(), 1),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.90),
                Classification(fiers[1][0], Classification.UP, 0.10),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.91)

    def test_tally_vote_equal_non_one_up_weights(self):
        fiers = [
                (StatusCodeClassifier(), 2),
                (StatusCodeClassifier(), 2)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.90),
                Classification(fiers[1][0], Classification.UP, 0.10),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.91)

    def test_tally_vote_diff_up_weights(self):
        fiers = [
                (StatusCodeClassifier(), 3),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.90),
                Classification(fiers[1][0], Classification.UP, 0.10),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.9033333333333333)

    def test_tally_vote_diff_up_weights_same_conf(self):
        fiers = [
                (StatusCodeClassifier(), 3),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.90),
                Classification(fiers[1][0], Classification.UP, 0.90),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.93)

    def test_tally_vote_3_diff_up_weights(self):
        fiers = [
                (StatusCodeClassifier(), 3),
                (StatusCodeClassifier(), 2),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.50),
                Classification(fiers[1][0], Classification.UP, 0.50),
                Classification(fiers[2][0], Classification.UP, 0.50),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.7222222222222222)

    def test_tally_vote_same_weights_diff_dir(self):
        fiers = [
                (StatusCodeClassifier(), 3),
                (StatusCodeClassifier(), 3)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.50),
                Classification(fiers[1][0], Classification.DOWN, 0.50),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.confidence, 0.0)

    def test_tally_vote_diff_weights_diff_dir(self):
        fiers = [
                (StatusCodeClassifier(), 3),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.50),
                Classification(fiers[1][0], Classification.DOWN, 0.50),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.25)

    def test_tally_vote_complex(self):
        fiers = [
                (StatusCodeClassifier(), 4),
                (StatusCodeClassifier(), 1),
                (StatusCodeClassifier(), 2),
                (StatusCodeClassifier(), 3),
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.80),
                Classification(fiers[1][0], Classification.UP, 0.70),
                Classification(fiers[2][0], Classification.DOWN, 0.90),
                Classification(fiers[3][0], Classification.DOWN, 0.60),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.DOWN)
        self.assertEqual(tally.confidence, 0.0024999999999999467)

    def test_tally_vote_zero_conf(self):
        fiers = [
                (StatusCodeClassifier(), 1),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.00),
                Classification(fiers[1][0], Classification.UP, 0.50),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.5)

    def test_tally_vote_zero_conf_diff_weight(self):
        fiers = [
                (StatusCodeClassifier(), 2),
                (StatusCodeClassifier(), 1)
                ]
        pipeline = ClassifyPipeline(fiers)
        classifications = [
                Classification(fiers[0][0], Classification.UP, 0.00),
                Classification(fiers[1][0], Classification.UP, 0.50),
                ]
        tally = pipeline.tally_vote(classifications)
        self.assertEqual(tally.direction, Classification.UP)
        self.assertEqual(tally.confidence, 0.25)

if __name__ == '__main__':
    unittest.main()
