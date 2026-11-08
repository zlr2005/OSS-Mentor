import unittest
import numpy as np
from coder_primary_comparison import bounded_score, standardize


class CoderPrimaryTests(unittest.TestCase):
    def test_auxiliary_cannot_exceed_declared_cap(self):
        base=np.array([-2.,0.,3.]);delta=np.array([100.,-100.,.1])
        score=bounded_score(base,delta)
        np.testing.assert_allclose(score-base,[.25,-.25,.1])

    def test_zero_auxiliary_preserves_coder_and_large_margin(self):
        base=np.array([3.,1.,0.])
        np.testing.assert_array_equal(bounded_score(base,np.zeros(3)),base)
        scores=bounded_score(base,np.array([-100.,100.,100.]))
        self.assertGreater(scores[0],scores[1])

    def test_normalization_preserves_order_and_handles_constant(self):
        np.testing.assert_array_equal(standardize(np.ones(3)),np.zeros(3))
        x=np.array([2.,-3.,1.])
        np.testing.assert_array_equal(np.argsort(x),np.argsort(standardize(x)))
        np.testing.assert_allclose(standardize(x)[[2,0,1]],standardize(x[[2,0,1]]))

    def test_nonfinite_auxiliary_rejected(self):
        with self.assertRaises(ValueError):bounded_score(np.ones(1),np.array([np.nan]))


if __name__=='__main__':unittest.main()
