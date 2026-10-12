"""The category classifier: TF-IDF features -> multinomial logistic regression -> a probability for every category.

Why this model:
  * Text is short and the vocabulary is open-ended, so linear models on sparse n-gram features are the standard strong
    baseline; they train in seconds on a laptop CPU and need no GPU.
  * Logistic regression gives probabilities, which we need for "how sure are we?" and for the review queue.
  * Its weights can be read: "which words pushed this towards Groceries" is the explanation we show the user.
  * With a few hundred examples a deep model would mostly memorise; the char-n-gram trick already gives spelling tolerance.
Full retraining is used instead of incremental updates (partial_fit): the data is small, so retraining takes seconds and keeps
the model explainable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .. import config


def effective_params(**overrides) -> dict:
    """The hyper-parameters actually used: config.CATEGORY_MODEL, overridden by keyword arguments (experiments / ablations)."""
    p = {**config.CATEGORY_MODEL, "use_name": True, "use_words": True, "use_ctx": True, **overrides}
    p["char_range"] = tuple(p["char_range"])
    return p


def make_pipeline(**overrides) -> Pipeline:
    p = effective_params(**overrides)
    C, ctx_weight, char_range = p["C"], p["ctx_weight"], p["char_range"]
    use_name, use_words, use_ctx = p["use_name"], p["use_words"], p["use_ctx"]
    parts, weights = [], {}
    if use_name:
        parts.append(("name", TfidfVectorizer(analyzer="char_wb", ngram_range=char_range, sublinear_tf=True), "name_text"))
        weights["name"] = 1.0
    if use_words:
        parts.append(("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), token_pattern=r"(?u)\b[A-Za-z0-9]{2,}\b",
                                               sublinear_tf=True), "words_text"))
        weights["words"] = 1.0
    if use_ctx:
        parts.append(("ctx", CountVectorizer(token_pattern=r"\S+", binary=True, lowercase=False), "ctx_text"))
        weights["ctx"] = ctx_weight
    ct = ColumnTransformer(parts, transformer_weights=weights, sparse_threshold=1.0)
    # newton-cg fits the very same multinomial logistic regression as lbfgs but ~10x faster on this sparse problem
    # (1.2 s vs 11.5 s for the cold-start set) - fast enough to re-learn after every user correction.
    clf = LogisticRegression(C=C, class_weight="balanced", solver="newton-cg", max_iter=200)
    return Pipeline([("features", ct), ("clf", clf)])


@dataclass
class Explanation:
    words: list[tuple[str, float]]        # (word, contribution to the predicted class)
    context: list[tuple[str, float]]
    neighbours: list[tuple[str, str, float]]   # (similar training name, its label, similarity)


class CategoryModel:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self.pipe: Pipeline | None = None
        self.sig: dict | None = None            # hyper-parameters this model was trained with (a saved model from other settings is refitted)
        self.classes_: list[str] = []
        self.n_train = 0
        self.n_user = 0
        self.trained_at = 0.0
        self.train_seconds = 0.0
        self._nn_names: list[str] = []
        self._nn_labels: list[str] = []
        self._nn_matrix = None

    # ---- training ------------------------------------------------------------------------------------------------------
    def fit(self, frame: pd.DataFrame, n_user: int = 0) -> "CategoryModel":
        t0 = time.time()
        self.pipe = make_pipeline(**self.kw)
        w = frame["weight"].to_numpy(dtype=float) if "weight" in frame else None
        self.pipe.fit(frame[["name_text", "words_text", "ctx_text"]], frame["label"], clf__sample_weight=w)
        self.classes_ = list(self.pipe.named_steps["clf"].classes_)
        self.n_train, self.n_user = len(frame), n_user
        self.sig = effective_params(**self.kw)
        self.trained_at, self.train_seconds = time.time(), time.time() - t0
        # remember unique names for "similar examples" explanations
        uniq = frame.drop_duplicates(["name_text", "label"])
        uniq = uniq[uniq["name_text"].str.len() > 0]
        self._nn_names, self._nn_labels = uniq["name_text"].tolist(), uniq["label"].tolist()
        vec = self._name_vectorizer()
        self._nn_matrix = vec.transform(self._nn_names) if vec is not None else None
        return self

    def _name_vectorizer(self):
        ct = self.pipe.named_steps["features"]
        return ct.named_transformers_.get("name") if "name" in ct.named_transformers_ else None

    # ---- inference -----------------------------------------------------------------------------------------------------
    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return self.pipe.predict_proba(frame[["name_text", "words_text", "ctx_text"]])

    def explain(self, frame_row: pd.DataFrame, top: int = 4) -> Explanation:
        """Why this prediction?  Per-word contributions (coef x tf-idf) and the most similar names the model was trained on."""
        ct = self.pipe.named_steps["features"]
        clf = self.pipe.named_steps["clf"]
        X = ct.transform(frame_row[["name_text", "words_text", "ctx_text"]])
        proba = clf.predict_proba(X)[0]
        k = int(np.argmax(proba))
        coef = clf.coef_[k]
        x = X.toarray()[0] if sparse.issparse(X) else X[0]
        names = ct.get_feature_names_out()
        contrib = coef * x
        words, ctx = [], []
        for i in np.argsort(-np.abs(contrib))[:60]:
            if abs(contrib[i]) < 0.02:
                break
            nm = str(names[i])
            if nm.startswith("words__"):
                words.append((nm[7:], float(contrib[i])))
            elif nm.startswith("ctx__"):
                ctx.append((nm[5:], float(contrib[i])))
        neigh: list[tuple[str, str, float]] = []
        vec = self._name_vectorizer()
        if vec is not None and self._nn_matrix is not None and frame_row["name_text"].iat[0]:
            q = vec.transform(frame_row["name_text"])
            sims = (self._nn_matrix @ q.T).toarray().ravel()
            for j in np.argsort(-sims)[:3]:
                if sims[j] > 0.2:
                    neigh.append((self._nn_names[j], self._nn_labels[j], float(sims[j])))
        return Explanation(words[:top], ctx[:3], neigh)
