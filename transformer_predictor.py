#!/usr/bin/env python3
"""
WhaleTransformer — Pure NumPy autoregressive transformer for whale behavior prediction.
No PyTorch/TF/JAX needed. Runs anywhere NumPy is installed.

Architecture:
  Input:  Sequence of whale events (tokenized as [event_type, entity, wallet_count, ...])
  Output: Next event prediction + conviction score

Reference: GPT-2 style decoder-only transformer, scaled down for time-series.
Paper: "Attention Is All You Need" (Vaswani et al.)
"""

import numpy as np
import pickle
import os
from typing import Tuple, Optional

# ── Model Config ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "vocab_size": 256,
    "d_model": 64,
    "n_heads": 4,
    "n_layers": 2,
    "d_ff": 256,
    "max_seq_len": 20,
    "dropout": 0.1,
}

# ── Event tokenizer ───────────────────────────────────────────

EVENT_TYPES = [
    "price_surge", "volume_spike", "agent_signal", "trade_executed",
    "whale_entry", "whale_exit", "cluster_form", "crowd_arrive",
    "price_move", "anomaly", "accumulation", "distribution",
    "scout_entry", "amplifier_entry", "leader_entry",
]

def tokenize_event(event_type: str, magnitude: float, entity_id: int,
                   wallet_count: int = 0, confidence: float = 0.5) -> np.ndarray:
    """Convert a whale event into a token embedding vector.
    Simple embedding: one-hot on event type + scaled features."""
    vec = np.zeros(DEFAULT_CONFIG["d_model"], dtype=np.float32)

    # Event type → first 32 dims
    if event_type in EVENT_TYPES:
        idx = EVENT_TYPES.index(event_type)
        vec[idx % 32] = 1.0

    # Numeric features → remaining dims
    vec[32] = np.tanh(magnitude)         # normalized magnitude
    vec[33] = entity_id / 100.0          # entity hash
    vec[34] = min(wallet_count / 10.0, 1.0)
    vec[35] = confidence
    vec[36] = np.sin(entity_id * 0.1)    # positional hint

    return vec


# ── Transformer Layers (Pure NumPy) ───────────────────────────

def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    x_max = np.max(x, axis=axis, keepdims=True)
    e_x = np.exp(x - x_max)
    return e_x / np.sum(e_x, axis=axis, keepdims=True)


def layer_norm(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray,
               eps: float = 1e-5) -> np.ndarray:
    mean = np.mean(x, axis=-1, keepdims=True)
    var = np.var(x, axis=-1, keepdims=True)
    return gamma * (x - mean) / np.sqrt(var + eps) + beta


def multi_head_attention(q: np.ndarray, k: np.ndarray, v: np.ndarray,
                         n_heads: int, mask: Optional[np.ndarray] = None,
                         wq: np.ndarray = None, wk: np.ndarray = None,
                         wv: np.ndarray = None, wo: np.ndarray = None
                         ) -> Tuple[np.ndarray, dict]:
    """Multi-head scaled dot-product attention. Pure NumPy."""
    batch, seq, d_model = q.shape
    d_k = d_model // n_heads

    # Use random weights if none provided (for fresh init)
    if wq is None:
        wq = np.random.randn(d_model, d_model) * 0.02
        wk = np.random.randn(d_model, d_model) * 0.02
        wv = np.random.randn(d_model, d_model) * 0.02
        wo = np.random.randn(d_model, d_model) * 0.02

    # Linear projections
    Q = q @ wq
    K = k @ wk
    V = v @ wv

    # Reshape to (batch, n_heads, seq, d_k)
    Q = Q.reshape(batch, seq, n_heads, d_k).transpose(0, 2, 1, 3)
    K = K.reshape(batch, seq, n_heads, d_k).transpose(0, 2, 1, 3)
    V = V.reshape(batch, seq, n_heads, d_k).transpose(0, 2, 1, 3)

    # Scaled dot-product
    scores = Q @ K.transpose(0, 1, 3, 2) / np.sqrt(d_k)

    # Causal mask (lower triangular)
    if mask is None:
        causal = np.tril(np.ones((seq, seq)))
        scores = np.where(causal == 0, -1e9, scores)

    attn_weights = softmax(scores, axis=-1)

    # Apply attention
    out = attn_weights @ V
    out = out.transpose(0, 2, 1, 3).reshape(batch, seq, d_model)
    out = out @ wo

    return out, {"weights": attn_weights, "wq": wq, "wk": wk, "wv": wv, "wo": wo}


def feed_forward(x: np.ndarray, w1: np.ndarray, b1: np.ndarray,
                 w2: np.ndarray, b2: np.ndarray) -> np.ndarray:
    """Position-wise feed-forward network."""
    return np.maximum(0, x @ w1 + b1) @ w2 + b2  # ReLU activation


class TransformerLayer:
    """Single transformer decoder layer."""

    def __init__(self, d_model: int, n_heads: int, d_ff: int):
        self.d_model = d_model
        self.n_heads = n_heads

        # Attention weights
        scale = np.sqrt(2.0 / d_model)
        self.wq = np.random.randn(d_model, d_model) * 0.02
        self.wk = np.random.randn(d_model, d_model) * 0.02
        self.wv = np.random.randn(d_model, d_model) * 0.02
        self.wo = np.random.randn(d_model, d_model) * 0.02

        # FFN weights
        self.ff_w1 = np.random.randn(d_model, d_ff) * scale
        self.ff_b1 = np.zeros(d_ff)
        self.ff_w2 = np.random.randn(d_ff, d_model) * scale
        self.ff_b2 = np.zeros(d_model)

        # Layer norms
        self.ln1_gamma = np.ones(d_model)
        self.ln1_beta = np.zeros(d_model)
        self.ln2_gamma = np.ones(d_model)
        self.ln2_beta = np.zeros(d_model)

    def forward(self, x: np.ndarray, mask: np.ndarray = None
                ) -> Tuple[np.ndarray, dict]:
        # Self-attention with residual
        attn_out, attn_info = multi_head_attention(
            x, x, x, self.n_heads, mask,
            self.wq, self.wk, self.wv, self.wo,
        )
        x = layer_norm(x + attn_out, self.ln1_gamma, self.ln1_beta)

        # Feed-forward with residual
        ff_out = feed_forward(x, self.ff_w1, self.ff_b1, self.ff_w2, self.ff_b2)
        x = layer_norm(x + ff_out, self.ln2_gamma, self.ln2_beta)

        return x, attn_info


# ── Full Model ────────────────────────────────────────────────

class WhaleTransformer:
    """Complete transformer for whale sequence prediction."""

    def __init__(self, config: dict = None):
        self.config = config or DEFAULT_CONFIG
        self.d_model = self.config["d_model"]
        self.n_layers = self.config["n_layers"]
        self.max_seq = self.config["max_seq_len"]

        # Positional embeddings
        self.pos_emb = np.random.randn(self.max_seq, self.d_model) * 0.02

        # Transformer layers
        self.layers = [
            TransformerLayer(self.d_model, self.config["n_heads"], self.config["d_ff"])
            for _ in range(self.n_layers)
        ]

        # Output head — predicts: [direction_buy, direction_sell, conviction, time_to_event]
        scale = np.sqrt(2.0 / self.d_model)
        self.out_w = np.random.randn(self.d_model, 4) * scale
        self.out_b = np.zeros(4)

    def forward(self, x: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Forward pass.
        Args:
            x: (batch, seq_len, d_model) token embeddings
        Returns:
            prediction: (batch, 4) — [p_buy, p_sell, conviction, eta_minutes]
            confidence: float
        """
        batch, seq, _ = x.shape
        seq = min(seq, self.max_seq)

        # Add positional embeddings
        x = x[:, :seq, :] + self.pos_emb[:seq, :][np.newaxis, :, :]

        # Through transformer layers
        for layer in self.layers:
            x, _ = layer.forward(x)

        # Take last token's output
        last = x[:, -1, :]

        # Output projection
        logits = last @ self.out_w + self.out_b
        probs = 1.0 / (1.0 + np.exp(-logits))  # sigmoid

        # Split: [p_buy, p_sell, conviction, eta]
        p_buy = probs[:, 0]
        p_sell = probs[:, 1]
        conviction = probs[:, 2]
        eta_minutes = probs[:, 3] * 120  # scale to 0-120 minutes

        # Direction
        direction = np.where(p_buy > p_sell, "BUY",
                    np.where(p_sell > p_buy + 0.1, "SELL", "WAIT"))

        return {
            "direction": direction[0] if len(direction.shape) > 0 else str(direction),
            "p_buy": float(p_buy.flat[0]) if p_buy.size > 0 else float(p_buy),
            "p_sell": float(p_sell.flat[0]) if p_sell.size > 0 else float(p_sell),
            "conviction": float(conviction.flat[0]) if conviction.size > 0 else float(conviction),
            "eta_minutes": float(eta_minutes.flat[0]) if eta_minutes.size > 0 else float(eta_minutes),
        }

    def predict(self, events: list) -> dict:
        """
        Predict next event from a sequence of whale events.
        Args:
            events: list of dicts with {type, magnitude, entity_id, wallet_count, confidence}
        """
        if not events:
            return {"direction": "WAIT", "conviction": 0.0, "eta_minutes": 0}

        # Tokenize each event
        tokens = []
        for e in events[-self.max_seq:]:
            vec = tokenize_event(
                e.get("type", "price_move"),
                e.get("magnitude", 0.0),
                hash(e.get("entity", "")) % 100,
                e.get("wallet_count", 0),
                e.get("confidence", 0.5),
            )
            tokens.append(vec)

        x = np.array(tokens, dtype=np.float32)[np.newaxis, :, :]
        return self.forward(x)

    def save(self, path: str):
        """Save model weights to file."""
        state = {
            "config": self.config,
            "pos_emb": self.pos_emb,
            "layers": [],
            "out_w": self.out_w,
            "out_b": self.out_b,
        }
        for layer in self.layers:
            state["layers"].append({
                "wq": layer.wq, "wk": layer.wk, "wv": layer.wv, "wo": layer.wo,
                "ff_w1": layer.ff_w1, "ff_b1": layer.ff_b1,
                "ff_w2": layer.ff_w2, "ff_b2": layer.ff_b2,
                "ln1_gamma": layer.ln1_gamma, "ln1_beta": layer.ln1_beta,
                "ln2_gamma": layer.ln2_gamma, "ln2_beta": layer.ln2_beta,
            })
        with open(path, "wb") as f:
            pickle.dump(state, f)

    @classmethod
    def load(cls, path: str) -> "WhaleTransformer":
        """Load model from file."""
        with open(path, "rb") as f:
            state = pickle.load(f)

        model = cls(state["config"])
        model.pos_emb = state["pos_emb"]
        model.out_w = state["out_w"]
        model.out_b = state["out_b"]

        for i, layer_state in enumerate(state["layers"]):
            model.layers[i].wq = layer_state["wq"]
            model.layers[i].wk = layer_state["wk"]
            model.layers[i].wv = layer_state["wv"]
            model.layers[i].wo = layer_state["wo"]
            model.layers[i].ff_w1 = layer_state["ff_w1"]
            model.layers[i].ff_b1 = layer_state["ff_b1"]
            model.layers[i].ff_w2 = layer_state["ff_w2"]
            model.layers[i].ff_b2 = layer_state["ff_b2"]
            model.layers[i].ln1_gamma = layer_state["ln1_gamma"]
            model.layers[i].ln1_beta = layer_state["ln1_beta"]
            model.layers[i].ln2_gamma = layer_state["ln2_gamma"]
            model.layers[i].ln2_beta = layer_state["ln2_beta"]

        return model


# ── Training ──────────────────────────────────────────────────

def train_step(model: WhaleTransformer, x: np.ndarray, y: np.ndarray,
               lr: float = 0.001) -> float:
    """
    One step of supervised training using simple gradient descent.
    y shape: (batch, 4) — [p_buy, p_sell, conviction, eta]
    """
    # Forward pass
    pred = model.forward(x)
    p_buy = np.array([[pred["p_buy"]]])
    p_sell = np.array([[pred["p_sell"]]])
    conv = np.array([[pred["conviction"]]])
    eta = np.array([[pred["eta_minutes"] / 120.0]])

    # Simple MSE loss
    loss = np.mean((p_buy - y[:, 0:1])**2 + (p_sell - y[:, 1:2])**2 +
                   (conv - y[:, 2:3])**2 + (eta - y[:, 3:4])**2)

    # Perturbation-based "gradient" (numerical approximation for pure NumPy)
    # In practice, use proper autograd or PyTorch. This is a demo training loop.
    eps = 0.001
    for layer in model.layers:
        # Perturb and check loss improvement
        for attr in ["wq", "wk", "wv", "wo"]:
            w = getattr(layer, attr)
            noise = np.random.randn(*w.shape) * eps
            setattr(layer, attr, w + noise)

            pred2 = model.forward(x)
            p_buy2 = np.array([[pred2["p_buy"]]])
            p_sell2 = np.array([[pred2["p_sell"]]])
            conv2 = np.array([[pred2["conviction"]]])
            eta2 = np.array([[pred2["eta_minutes"] / 120.0]])
            loss2 = np.mean((p_buy2 - y[:, 0:1])**2 + (p_sell2 - y[:, 1:2])**2 +
                           (conv2 - y[:, 2:3])**2 + (eta2 - y[:, 3:4])**2)

            if loss2 < loss:
                # Keep the perturbation
                loss = loss2
            else:
                # Revert
                setattr(layer, attr, w)

    return float(loss)


def generate_training_data(whale_events: list, num_samples: int = 100) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate training data from whale event sequences.
    X: sequences of tokenized events
    Y: next event labels [buy_prob, sell_prob, conviction, eta]
    """
    X, Y = [], []

    for _ in range(num_samples):
        seq_len = min(len(whale_events), np.random.randint(5, 20))
        if seq_len < 2:
            continue

        # Random slice of whale events
        start = np.random.randint(0, max(1, len(whale_events) - seq_len))
        seq = whale_events[start:start + seq_len]

        # Tokenize
        tokens = []
        for e in seq:
            vec = tokenize_event(
                e.get("type", "price_move"),
                e.get("magnitude", 0.0),
                hash(e.get("entity", "")) % 100,
                e.get("wallet_count", 0),
                e.get("confidence", 0.5),
            )
            tokens.append(vec)

        # Pad to max_seq
        while len(tokens) < DEFAULT_CONFIG["max_seq_len"]:
            tokens.append(np.zeros(DEFAULT_CONFIG["d_model"], dtype=np.float32))

        X.append(np.array(tokens, dtype=np.float32))

        # Label: was next event a pump?
        next_event = whale_events[start + seq_len] if start + seq_len < len(whale_events) else whale_events[-1]
        y_buy = 1.0 if next_event.get("type") in ("price_surge", "crowd_arrive") else 0.0
        y_sell = 1.0 if next_event.get("type") in ("whale_exit", "distribution") else 0.0
        y_conv = next_event.get("conviction", 0.5)
        y_eta = min(next_event.get("eta", 30), 120) / 120.0

        Y.append([y_buy, y_sell, y_conv, y_eta])

    return np.array(X, dtype=np.float32), np.array(Y, dtype=np.float32)


# ── Convenience ───────────────────────────────────────────────

# Singleton model instance
_model: Optional[WhaleTransformer] = None
_model_path = os.path.join(os.path.dirname(__file__), "whale_model.pkl")


def get_model() -> WhaleTransformer:
    """Get or create the global model instance."""
    global _model
    if _model is None:
        if os.path.exists(_model_path):
            _model = WhaleTransformer.load(_model_path)
        else:
            _model = WhaleTransformer()
            _model.save(_model_path)
    return _model


def predict_from_events(events: list) -> dict:
    """Convenience: predict next move from whale events."""
    return get_model().predict(events)
