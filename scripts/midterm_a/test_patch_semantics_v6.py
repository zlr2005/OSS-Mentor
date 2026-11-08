import unittest
import numpy as np
import torch
from patch_semantics_v6 import pca_features, SemanticHead


class SemanticsTest(unittest.TestCase):
    def test_pca_ignores_validation_and_unavailable_training(self):
        matrix=np.random.default_rng(1).normal(size=(60,384)).astype(np.float32)
        available=np.ones(60,dtype=bool)
        available[39]=False
        a,pa=pca_features(matrix,list(range(40)),available)
        matrix[39:]*=-100
        b,pb=pca_features(matrix,list(range(40)),available)
        self.assertEqual(pa,pb)
        self.assertTrue(torch.equal(a[:39],b[:39]))
        self.assertEqual(float(b[39].abs().sum()),0)

    def test_insufficient_pca_data_rejected(self):
        with self.assertRaises(ValueError):
            pca_features(np.ones((32,384)),list(range(32)),np.ones(32,dtype=bool))

    def test_missing_semantic_fallback_after_learning(self):
        head=SemanticHead()
        with torch.no_grad():head.net[-1].bias.fill_(1)
        base=torch.randn(2,32)
        result=head(base,torch.randn(2,23),torch.ones(2,23,dtype=torch.bool),torch.randn(2,32),torch.zeros(2,dtype=torch.bool))
        self.assertTrue(torch.equal(base,result))


if __name__=='__main__':unittest.main()
