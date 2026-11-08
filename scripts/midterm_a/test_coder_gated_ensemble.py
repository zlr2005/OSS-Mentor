import unittest
import numpy as np
from evaluate_coder_gated_ensemble import equal_ensemble


class EnsembleTests(unittest.TestCase):
    def test_per_query_affine_invariance_and_seed_symmetry(self):
        a=np.array([1.,2.,3.,9.,7.],dtype=np.float32)
        b=np.array([4.,1.,2.,3.,5.],dtype=np.float32)
        slices=[(0,3),(3,5)]
        expected=equal_ensemble([a,b],slices)
        shifted=a.copy();shifted[:3]=a[:3]*2+100;shifted[3:]=a[3:]*3-20
        np.testing.assert_allclose(expected,equal_ensemble([shifted,b],slices),atol=1e-6)
        np.testing.assert_allclose(expected,equal_ensemble([b,a],slices),atol=1e-7)

    def test_constants_are_finite_and_nan_is_rejected(self):
        np.testing.assert_array_equal(equal_ensemble([np.ones(3)],[(0,3)]),np.zeros(3))
        with self.assertRaises(ValueError):equal_ensemble([np.array([np.nan])],[(0,1)])


if __name__=='__main__':unittest.main()
