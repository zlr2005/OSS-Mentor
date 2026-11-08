import copy
from datetime import datetime,timedelta,timezone
import unittest
import torch
from train_coder_multi_positive import additional_positives,multi_loss


class MultiPositiveTests(unittest.TestCase):
    def test_masked_single_matches_original21_loss_and_ignores_extra(self):
        torch.manual_seed(3);pred=torch.randn(3,25,requires_grad=True)
        valid=torch.ones((3,25),dtype=torch.bool);positive=torch.zeros_like(valid);positive[:,0]=True;positive[:,21:]=True
        weights=torch.tensor([1.,.5,.25])
        actual=multi_loss(pred,valid,positive,weights,'masked_single')
        expected=((torch.nn.functional.cross_entropy(pred[:,:21],torch.zeros(3,dtype=torch.long),reduction='none')+.01*pred[:,:21].square().mean(1))*weights).sum()/weights.sum()
        self.assertAlmostEqual(float(actual.detach()),float(expected.detach()),places=6)
        actual.backward();self.assertEqual(float(pred.grad[:,21:].abs().sum()),0)

    def test_multi_objectives_respect_positive_set_and_padding(self):
        pred=torch.zeros((1,5),requires_grad=True);valid=torch.tensor([[True,True,True,True,False]])
        pos=torch.tensor([[True,False,False,True,False]])
        for arm in ['uniform_positive','marginal_positive']:
            pred.grad=None;loss=multi_loss(pred,valid,pos,torch.ones(1),arm);loss.backward()
            self.assertLess(float(pred.grad[0,0]),0);self.assertLess(float(pred.grad[0,3]),0)
            self.assertGreater(float(pred.grad[0,1]),0);self.assertEqual(float(pred.grad[0,4]),0)
        with self.assertRaises(ValueError):multi_loss(pred,valid,torch.ones_like(valid),torch.ones(1),'uniform_positive')

    def test_train_identity_strict_interaction_and90day_boundary(self):
        at=datetime(2024,4,1,tzinfo=timezone.utc);stamp=lambda n:(at+timedelta(days=n)).isoformat()
        sample=dict(positive_issue_key='r/x#1',as_of=stamp(0),github_numeric_id=7)
        c={'r/x#2':dict(available_from=stamp(-1),available_until=stamp(100))}
        label=dict(verification_status='verified',issue_key='r/x#2',interaction_at=stamp(1),outcome_at=stamp(90))
        row=dict(split='train',positive_issue_key='r/x#2',github_numeric_id=7,label_source=label,outcome_id='verified2')
        self.assertEqual(additional_positives(sample,[row],c),{'r/x#2':'verified2'})
        for field,value in [('interaction_at',stamp(0)),('outcome_at',stamp(91))]:
            changed=copy.deepcopy(row);changed['label_source'][field]=value
            self.assertEqual(additional_positives(sample,[changed],c),{})
        for field,value in [('split','validation'),('github_numeric_id',8)]:
            changed=dict(row,**{field:value})
            with self.assertRaises(ValueError):additional_positives(sample,[changed],c)

    def test_candidate_closed_at_cutoff_is_not_added(self):
        sample=dict(positive_issue_key='r/x#1',as_of='2024-04-01T00:00:00Z',github_numeric_id=7)
        row=dict(split='train',positive_issue_key='r/x#2',github_numeric_id=7,outcome_id='two',label_source=dict(
            verification_status='verified',issue_key='r/x#2',interaction_at='2024-04-02T00:00:00Z',outcome_at='2024-04-03T00:00:00Z'))
        c={'r/x#2':dict(available_from='2024-03-01T00:00:00Z',available_until=sample['as_of'])}
        self.assertEqual(additional_positives(sample,[row],c),{})


if __name__=='__main__':unittest.main()
