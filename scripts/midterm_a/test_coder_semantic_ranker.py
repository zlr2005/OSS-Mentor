import unittest
import numpy as np
import torch
from prepare_coder_semantic_matches import semantic_matches
from train_coder_semantic_ranker import same_repo_loss,GatedFusion


class SemanticRankingTests(unittest.TestCase):
    def test_best_file_retains_signal_cancelled_by_average(self):
        files=np.array([[1.,0.],[-1.,0.]],dtype=np.float32)
        values=semantic_matches(np.zeros(2),files,np.array([1.,0.]),np.array([1.,0.]))
        self.assertEqual(values[0],0)
        self.assertEqual(values[1:3],[1.,1.])
        reversed_values=semantic_matches(np.zeros(2),files[::-1],np.array([1.,0.]),np.array([1.,0.]))
        np.testing.assert_allclose(values[:4],reversed_values[:4])
        self.assertEqual(reversed_values[4],-1)

    def test_same_repo_loss_ignores_cross_repo_and_has_no_singleton_penalty(self):
        scores=torch.tensor([[1.,2.,100.],[1.,100.,200.]],requires_grad=True)
        mask=torch.tensor([[True,True,False],[True,False,False]])
        loss=same_repo_loss(scores,mask)
        self.assertEqual(float(loss[1].detach()),0)
        loss.sum().backward()
        self.assertEqual(float(scores.grad[0,2]),0)
        self.assertEqual(float(scores.grad[1].abs().sum()),0)
        with self.assertRaises(ValueError):same_repo_loss(scores,torch.zeros_like(mask))

    def test_extended_model_uses_coder_and_accepts_semantic_features(self):
        model=GatedFusion(22);x=torch.randn(2,21,150);base=torch.ones(2,21)
        a=model(x,base);b=model(x,-base)
        self.assertEqual(tuple(a.shape),(2,21))
        self.assertTrue(bool(torch.isfinite(a).all()))
        self.assertFalse(torch.equal(a,b))


if __name__=='__main__':unittest.main()
