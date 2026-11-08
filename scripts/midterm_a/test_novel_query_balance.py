import unittest
import numpy as np
from train_novel_query_balance import balanced_query_weights


class QueryBalanceTest(unittest.TestCase):
    def test_mass_preservation_and_equal_class_mass(self):
        base=np.array([1.,.5,.5,1.]);novel=np.array([True,False,False,False])
        result=balanced_query_weights(base,novel)
        self.assertAlmostEqual(result.sum(),base.sum())
        self.assertAlmostEqual(result[novel].sum(),result[~novel].sum())
        self.assertAlmostEqual(result[1]/result[2],base[1]/base[2])
        self.assertTrue(np.isfinite(result).all())

    def test_one_class_cannot_be_balanced(self):
        with self.assertRaises(ValueError):balanced_query_weights([1,1],[False,False])
        with self.assertRaises(ValueError):balanced_query_weights([1,1],[True,True])


if __name__=='__main__':unittest.main()
