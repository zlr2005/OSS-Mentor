import unittest
import torch
from train_public_text_curve import TextRanker,metrics


class RankerTest(unittest.TestCase):
    def test_finite_gradients_and_reload(self):
        torch.manual_seed(1);m=TextRanker();u=torch.randn(4,430);i=torch.randn(4,21,384)
        score=m(u,i);loss=-torch.nn.functional.logsigmoid(score[:,:1]-score[:,1:]).mean();loss.backward()
        self.assertTrue(all(p.grad is not None and torch.isfinite(p.grad).all() for p in m.parameters()))
        n=TextRanker();n.load_state_dict(m.state_dict());self.assertTrue(torch.equal(score,n(u,i)))

    def test_developer_macro_not_outcome_weighted(self):
        r=metrics([1,1,10],[dict(github_numeric_id=1),dict(github_numeric_id=1),dict(github_numeric_id=2)])
        self.assertAlmostEqual(r['developer_macro_mrr'],.55)
        self.assertAlmostEqual(r['mrr'],.7)


if __name__=='__main__':unittest.main()
