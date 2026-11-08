import unittest
import torch
from train_coder_full_pool import training_pool,batch_queries,listwise_loss


class FullPoolTests(unittest.TestCase):
    def test_exact_cutoff_and_known_success_exclusion_without_injection(self):
        candidates=[dict(issue_key=k,available_from=start,available_until=end) for k,start,end in [
            ('r/x#1','2024-01-01T00:00:00Z','2025-01-01T00:00:00Z'),
            ('r/x#2','2024-01-01T00:00:00Z','2025-01-01T00:00:00Z'),
            ('r/x#3','2024-06-02T00:00:00Z','2025-01-01T00:00:00Z'),
            ('r/x#4','2024-01-01T00:00:00Z','2024-06-01T00:00:00Z')]]
        sample=dict(positive_issue_key='r/x#1',as_of='2024-06-01T00:00:00Z')
        self.assertEqual(training_pool(sample,candidates,{'r/x#1','r/x#2'}),['r/x#1'])
        with self.assertRaises(ValueError):training_pool(dict(sample,positive_issue_key='r/x#3'),candidates,set())

    def test_variable_targets_and_padding_do_not_change_loss_or_gradient(self):
        pred=torch.tensor([[1.,2.,900.],[3.,4.,5.]],requires_grad=True)
        mask=torch.tensor([[True,True,False],[True,True,True]])
        targets=torch.tensor([1,2]);weights=torch.ones(2)
        loss=listwise_loss(pred,mask,targets,weights)
        expected=(listwise_loss(pred[:1,:2],mask[:1,:2],targets[:1],weights[:1])+
                  listwise_loss(pred[1:],mask[1:],targets[1:],weights[1:]))/2
        self.assertAlmostEqual(float(loss.detach()),float(expected.detach()),places=6)
        loss.backward();self.assertEqual(float(pred.grad[0,2]),0)

    def test_batch_keeps_candidate_order_and_nonzero_target_index(self):
        x=torch.arange(10,dtype=torch.float32).reshape(5,2);base=torch.arange(5,dtype=torch.float32)
        xb,bb,mask,targets=batch_queries(x,base,[(0,2),(2,5)],[1,2],[1,0])
        self.assertEqual(targets.tolist(),[2,1])
        self.assertEqual(mask.tolist(),[[True,True,True],[True,True,False]])
        self.assertTrue(torch.equal(xb[0],x[2:]))
        with self.assertRaises(ValueError):listwise_loss(bb,mask,torch.tensor([2,2]),torch.ones(2))


if __name__=='__main__':unittest.main()
