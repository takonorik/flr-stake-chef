import importlib.util
import pathlib
import unittest

_path = pathlib.Path(__file__).resolve().parent.parent / "build-apr.py"
_spec = importlib.util.spec_from_file_location("build_apr", _path)
b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b)


class NodeIdToHex(unittest.TestCase):
    def test_matches_fsp_node_id(self):
        # FSP の reward-epoch-info.json の nodeIds に実在する組み合わせ
        self.assertEqual(
            b.node_id_to_hex("NodeID-4qa2aH7gEksskHU2nYna1mNX37U1yfdAp"),
            "0x2a17e9c62afa4b299606ad20c30b581f30b3268b",
        )

    def test_rejects_bad_checksum(self):
        with self.assertRaises(ValueError):
            b.node_id_to_hex("NodeID-4qa2aH7gEksskHU2nYna1mNX37U1yfdAq")


class ChooseWindow(unittest.TestCase):
    def test_plain_window_when_stable(self):
        pools = {e: 0.05 for e in range(430, 436)}
        self.assertEqual(b.choose_window(pools, 4), ([432, 433, 434, 435], None))

    def test_gradual_drift_is_not_a_break(self):
        pools = {430: 0.060, 431: 0.058, 432: 0.056, 433: 0.054, 434: 0.052, 435: 0.050}
        self.assertEqual(b.choose_window(pools, 4)[1], None)

    def test_shrinks_after_step_change(self):
        # Granite: MIRROR原資が約4倍になった
        pools = {415: 0.02, 416: 0.02, 417: 0.08, 418: 0.08, 419: 0.08}
        self.assertEqual(b.choose_window(pools, 4), ([417, 418, 419], 417))

    def test_step_outside_window_is_ignored(self):
        pools = {416: 0.02, 417: 0.08, 418: 0.08, 419: 0.08, 420: 0.08, 421: 0.08}
        self.assertEqual(b.choose_window(pools, 4), ([418, 419, 420, 421], None))

    def test_skipped_epoch_is_tolerated(self):
        pools = {430: 0.05, 432: 0.05, 433: 0.05, 435: 0.05}
        self.assertEqual(b.choose_window(pools, 4)[0], [430, 432, 433, 435])


class MirrorElasticity(unittest.TestCase):
    @staticmethod
    def synthetic(beta, n_ent=40, n_ep=4):
        # 原資 = ネットワーク要因(エポックごとに変動) × ステーク^beta
        epochs = []
        for t in range(n_ep):
            stake = {f"e{i}": 1e6 * (1 + i) * (1 + 0.05 * ((i * 7 + t * 3) % 11)) for i in range(n_ent)}
            pool = {k: (0.9 ** t) * v ** beta for k, v in stake.items()}
            epochs.append((pool, stake))
        return epochs

    def test_recovers_known_elasticity(self):
        for beta in (0.0, 0.5, 0.85, 1.0):
            est, n = b.mirror_elasticity(self.synthetic(beta))
            self.assertAlmostEqual(est, beta, places=6)
            self.assertGreaterEqual(n, b.MIN_ELASTICITY_SAMPLES)

    def test_falls_back_when_too_few_samples(self):
        est, n = b.mirror_elasticity(self.synthetic(0.3, n_ent=5, n_ep=2))
        self.assertEqual(est, b.DEFAULT_ELASTICITY)

    def test_entity_pools_groups_nodes(self):
        pool, stake = b.entity_pools({"n1": 10.0, "n2": 5.0}, {"n1": 100.0, "n2": 50.0, "n3": 0.0},
                                     {"n1": "A", "n2": "A", "n3": "B"})
        self.assertEqual((pool["A"], stake["A"]), (15.0, 150.0))
        self.assertNotIn("B", stake)


class Unchanged(unittest.TestCase):
    def test_ignores_generated_at(self):
        a = {"generatedAt": "2026-09-25", "epochs": [1], "nodes": {"x": 1}}
        c = {"generatedAt": "2026-09-26", "epochs": [1], "nodes": {"x": 1}}
        self.assertTrue(b.unchanged(a, c))

    def test_detects_node_change(self):
        a = {"generatedAt": "t", "epochs": [1], "nodes": {"x": 1}}
        c = {"generatedAt": "t", "epochs": [1], "nodes": {"x": 2}}
        self.assertFalse(b.unchanged(a, c))


if __name__ == "__main__":
    unittest.main()
