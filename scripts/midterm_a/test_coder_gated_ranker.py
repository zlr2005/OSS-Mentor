import unittest
import torch
from train_coder_gated_ranker import GatedFusion,train_head


class GatedFusionTests(unittest.TestCase):
    def test_real_coder_signal_and_bounded_contribution(self):
        model=GatedFusion();x=torch.zeros(2,21,144)
        base=torch.ones(2,21,requires_grad=True)
        pair,delta,gate=model.components(x,base)
        self.assertTrue(bool((delta>0).all()))
        self.assertTrue(bool((gate>0).all()) and bool((gate<1).all()))
        self.assertLessEqual(float(delta.abs().max().detach()),.25)
        self.assertFalse(torch.equal(model(x,base),model(x,-base)))
        model(x,base).sum().backward()
        self.assertGreater(float(base.grad.abs().sum()),0)
        self.assertGreater(float(model.embedding[-1].weight.grad.abs().sum()),0)

    def test_coder_embedding_channel_is_not_disconnected(self):
        model=GatedFusion();x=torch.randn(2,21,144,requires_grad=True)
        with torch.no_grad():model.embedding[-1].weight.fill_(.1)
        model(x,torch.zeros(2,21)).sum().backward()
        self.assertGreater(float(x.grad[...,:128].abs().sum()),0)

    def test_neural_pair_control_keeps_same_learning_objective(self):
        torch.set_num_threads(1);x=torch.zeros(32,21,16);x[:,0,0]=1
        model,history=train_head(x,torch.zeros(32,21),torch.ones(32),7,8)
        with torch.no_grad():scores=model(x,torch.zeros(32,21))
        self.assertTrue(bool((scores[:,0]>scores[:,1]).all()))
        self.assertLess(history[-1]['train_loss'],history[0]['train_loss'])


if __name__=='__main__':unittest.main()
