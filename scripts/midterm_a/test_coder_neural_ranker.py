import unittest
import numpy as np
import torch
from train_coder_neural_ranker import NeuralHead,select_controls,train_head


class NeuralRankerTests(unittest.TestCase):
    def test_zero_initialization_and_residual_bound(self):
        model=NeuralHead(128);x=torch.randn(3,21,128);base=torch.randn(3,21)
        self.assertTrue(torch.equal(model(x,base),base))
        with torch.no_grad():model.net[-1].bias.fill_(1000)
        self.assertLessEqual(float((model(x,base)-base).abs().max().detach()),2.000001)

    def test_controls_exclude_known_positive_and_future_candidate(self):
        keys=['r/x#'+str(i) for i in range(35)]
        sample=dict(positive_issue_key=keys[0],comparison_issue_keys=keys[1:21])
        result=select_controls(sample,keys[:30],{keys[0],keys[1]},dict(zip(keys,range(35))))
        selected=result['comparison_issue_keys']
        self.assertEqual(len(selected),len(set(selected)))
        self.assertEqual(len(selected),20)
        self.assertFalse(set(selected)&{keys[0],keys[1]})
        self.assertTrue(set(selected)<=set(keys[:30]))
        self.assertEqual(sample['comparison_issue_keys'],keys[1:21])

    def test_head_can_learn_query_ranking_from_interactions(self):
        torch.set_num_threads(1)
        x=torch.zeros(32,21,1);x[:,0,0]=1;base=torch.zeros(32,21)
        model,history=train_head(x,base,torch.ones(32),7,8)
        with torch.no_grad():pred=model(x,base)
        self.assertTrue(bool((pred[:,0]>pred[:,1]).all()))
        self.assertLess(history[-1]['train_loss'],history[0]['train_loss'])


if __name__=='__main__':unittest.main()
