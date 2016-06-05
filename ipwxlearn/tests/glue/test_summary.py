# -*- coding: utf-8 -*-
import unittest

import numpy as np

from ipwxlearn import glue
from ipwxlearn.glue import G
from ipwxlearn.utils import tempdir
from ipwxlearn.utils.training import run_steps
from ipwxlearn.utils.training.monitors import SummaryMonitor, ValidationMonitor


class SummaryTestCase(unittest.TestCase):

    @staticmethod
    def make_softmax_data(n=10000, dim=10, target_num=2, dtype=np.float64):
        if target_num > 2:
            W = (np.random.random([dim, target_num]) - 0.5).astype(dtype)
            b = (np.random.random([target_num]) - 0.5).astype(dtype)
            X = ((np.random.random([n, dim]) - 0.5) * 10.0).astype(dtype)
            y = np.argmax(np.dot(X, W) + b, axis=1).astype(np.int32)
            return (W, b), (X, y)
        else:
            W = (np.random.random([dim, 1]) - 0.5).astype(dtype)
            b = (np.random.random([1]) - 0.5).astype(dtype)
            X = ((np.random.random([n, dim]) - 0.5) * 10.0).astype(dtype)
            logits = (np.dot(X, W) + b).reshape([X.shape[0]])
            y = (1.0 / (1 + np.exp(-logits)) >= 0.5).astype(np.int32)
            return (W, b), (X, y)

    def test_okay(self):
        """No validation on the summary, just test whether or not the summary writer could run."""

        target_num = 2
        (W, b), (X, y) = self.make_softmax_data(n=1500, target_num=target_num, dtype=glue.config.floatX)
        idx = np.arange(len(X))
        np.random.shuffle(idx)
        (train_X, train_y), (valid_X, valid_y) = (X[idx[:1000]], y[idx[:1000]]), (X[idx[1000:]], y[idx[1000:]])

        graph = G.Graph()
        with graph.as_default():
            input_var = G.make_placeholder('inputs', shape=(None, W.shape[0]), dtype=glue.config.floatX)
            label_var = G.make_placeholder('labels', shape=(None,), dtype=np.int32)
            input_layer = G.layers.InputLayer(input_var, shape=(None, W.shape[0]))
            softmax_layer = G.layers.SoftmaxLayer('softmax', input_layer, num_units=target_num)
            output, loss = G.layers.get_output_with_sparse_softmax_crossentropy(softmax_layer, label_var)
            loss = G.op.mean(loss)

            summaries = G.summary.collect_variable_summaries()

            updates = G.updates.adam(loss, G.layers.get_all_params(softmax_layer, trainable=True))
            training_loss_summary = G.summary.scalar_summary('training_loss', loss)
            train_fn = G.make_function(inputs=[input_var, label_var], outputs=[loss, training_loss_summary],
                                       updates=updates)
            valid_loss_summary = G.summary.scalar_summary('validation_loss', loss)
            valid_fn = G.make_function(inputs=[input_var, label_var], outputs=[loss, valid_loss_summary])

        with G.Session(graph):
            with tempdir.TemporaryDirectory() as path:
                writer = G.summary.SummaryWriter(path)
                # test unmerged summaries
                run_steps(G, train_fn, (train_X, train_y), max_steps=500, summary_writer=writer, monitor=[
                    ValidationMonitor(valid_fn, (valid_X, valid_y), steps=50, summary_writer=writer),
                    SummaryMonitor(writer, summaries, steps=100)
                ])

        with G.Session(graph):
            with tempdir.TemporaryDirectory() as path:
                writer = G.summary.SummaryWriter(path)
                # test merged summaries
                run_steps(G, train_fn, (train_X, train_y), max_steps=500, summary_writer=writer, monitor=[
                    ValidationMonitor(valid_fn, (valid_X, valid_y), steps=50, summary_writer=writer),
                    SummaryMonitor(writer, G.summary.merge_summary(summaries), steps=100)
                ])
