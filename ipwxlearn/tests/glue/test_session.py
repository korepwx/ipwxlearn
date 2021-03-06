# -*- coding: utf-8 -*-
import os
import unittest

import numpy as np

from ipwxlearn.glue import G
from ipwxlearn.utils.misc import assert_raises_message
from ipwxlearn.utils.tempdir import TemporaryDirectory


class SessionTestCase(unittest.TestCase):

    def test_session_vars(self):
        """Test save/restore/init variables for a session."""
        graph = G.Graph()

        with graph.as_default():
            a = G.make_variable('a', (), 1, dtype=np.int32, persistent=True)
            b = G.make_variable('b', (), 2, dtype=np.int32, persistent=True)
            c = G.make_variable('c', (), 3, dtype=np.int32)

        # some other tests might pollute the graph stack,
        # so we purge everything from the graph stack before entering this.
        from ipwxlearn.glue.common.graph import _graph_stack
        from ipwxlearn.glue.common.scope import _name_scope_stack
        from ipwxlearn.glue.common.session import _session_stack

        while not _graph_stack.empty:
            _graph_stack.pop()
        while not _name_scope_stack.empty:
            _name_scope_stack.pop()
        while not _session_stack.empty:
            _session_stack.pop()

        with assert_raises_message(self, ValueError, 'No graph is activated.'):
            with G.Session():
                pass

        with graph.as_default():
            with G.Session():
                self.assertEqual(G.get_variable_values([a, b, c]), (1, 2, 3))
                G.set_variable_values({a: 10, b: 20, c: 30})
                self.assertEqual(G.get_variable_values([a, b, c]), (10, 20, 30))
        self.assertEqual(graph.get_last_values([a, b, c]), (10, 20, None))
        self.assertDictEqual(graph.get_last_values_as_dict([a, b, c]), {a: 10, b: 20})

        with G.Session(graph):
            self.assertEqual(G.get_variable_values([a, b, c]), (10, 20, 3))
        self.assertEqual(graph.get_last_values([a, b, c]), (10, 20, None))

        with G.Session(graph, feed_values={b: 200, c: 300}):
            self.assertEqual(G.get_variable_values([a, b, c]), (10, 200, 300))
        self.assertEqual(graph.get_last_values([a, b, c]), (10, 200, None))

        with G.Session(graph):
            self.assertEqual(G.get_variable_values([a, b, c]), (10, 200, 3))
        self.assertEqual(graph.get_last_values([a, b, c]), (10, 200, None))

        with G.Session(graph, init_variables=True):
            self.assertEqual(G.get_variable_values([a, b, c]), (1, 2, 3))
        self.assertEqual(graph.get_last_values([a, b, c]), (1, 2, None))

    def test_checkpoint(self):
        graph = G.Graph()

        with graph.as_default():
            a = G.make_variable('a', (), 1, dtype=np.int32, trainable=True)
            b = G.make_variable('b', (), 2, dtype=np.int32, persistent=True)
            c = G.make_variable('c', (), 3, dtype=np.int32, resumable=True)
            d = G.make_variable('d', (), 4, dtype=np.int32)

        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'checkpoint.pkl')

            with G.Session(graph, checkpoint_file=path, max_checkpoints=3) as sess:
                self.assertEqual(sess.next_checkpoint_index, 1)

                G.set_variable_values({a: 10, b: 20, c: 30, d: 40})
                sess.memo['a'] = 100
                sess.memo['b'] = 999
                sess.checkpoint()
                self.assertEqual(sess.next_checkpoint_index, 2)

                self.assertTrue(os.path.isfile('%s.v1' % path))
                self.assertTrue(os.path.isfile('%s.m1' % path))

            with G.Session(graph, checkpoint_file=path, max_checkpoints=3) as sess:
                self.assertEqual(sess.next_checkpoint_index, 2)
                self.assertEqual(G.get_variable_values([a, b, c, d]), (10, 20, 30, 4))
                self.assertEqual((sess.memo['a'], sess.memo['b']), (100, 999))

                G.set_variable_values({a: 11, b: 21, c: 31, d: 41})
                sess.checkpoint()
                self.assertEqual(sess.next_checkpoint_index, 3)

                self.assertTrue(os.path.isfile('%s.v1' % path))
                self.assertTrue(os.path.isfile('%s.v2' % path))

                self.assertTrue(os.path.isfile('%s.m1' % path))
                self.assertFalse(os.path.isfile('%s.m2' % path))

            with G.Session(graph) as sess:
                self.assertEqual(sess.next_checkpoint_index, 1)
                self.assertNotIn('a', sess.memo)
                self.assertNotIn('b', sess.memo)
                self.assertEqual(G.get_variable_values([a, b, c, d]), (11, 21, 3, 4))
                G.set_variable_values({a: 100, b: 200, c: 300, d: 400})
                sess.memo['a'] = -1
                sess.memo['b'] = -2

            with G.Session(graph, checkpoint_file=path, max_checkpoints=3) as sess:
                self.assertEqual(sess.next_checkpoint_index, 3)
                self.assertEqual(G.get_variable_values([a, b, c, d]), (11, 21, 31, 4))
                self.assertEqual((sess.memo['a'], sess.memo['b']), (100, 999))

                G.set_variable_values({a: 12, b: 22, c: 32, d: 42})
                sess.memo['a'] = 101
                sess.checkpoint()
                self.assertEqual(sess.next_checkpoint_index, 4)

                self.assertTrue(os.path.isfile('%s.v1' % path))
                self.assertTrue(os.path.isfile('%s.v2' % path))
                self.assertTrue(os.path.isfile('%s.v3' % path))

                self.assertTrue(os.path.isfile('%s.m1' % path))
                self.assertFalse(os.path.isfile('%s.m2' % path))
                self.assertTrue(os.path.isfile('%s.m3' % path))

                G.set_variable_values({a: 13, b: 23, c: 33, d: 43})
                sess.checkpoint()
                self.assertEqual(sess.next_checkpoint_index, 5)

                self.assertFalse(os.path.isfile('%s.v1' % path))
                self.assertTrue(os.path.isfile('%s.v2' % path))
                self.assertTrue(os.path.isfile('%s.v3' % path))
                self.assertTrue(os.path.isfile('%s.v4' % path))

                self.assertTrue(os.path.isfile('%s.m1' % path))
                self.assertFalse(os.path.isfile('%s.m2' % path))
                self.assertTrue(os.path.isfile('%s.m3' % path))
                self.assertFalse(os.path.isfile('%s.m4' % path))

            with G.Session(graph, checkpoint_file=path, max_checkpoints=2) as sess:
                self.assertEqual(sess.next_checkpoint_index, 5)
                self.assertEqual(G.get_variable_values([a, b, c, d]), (13, 23, 33, 4))
                self.assertEqual((sess.memo['a'], sess.memo['b']), (101, 999))

                sess.checkpoint()
                self.assertEqual(sess.next_checkpoint_index, 6)

                self.assertFalse(os.path.isfile('%s.v1' % path))
                self.assertFalse(os.path.isfile('%s.v2' % path))
                self.assertFalse(os.path.isfile('%s.v3' % path))
                self.assertTrue(os.path.isfile('%s.v4' % path))
                self.assertTrue(os.path.isfile('%s.v5' % path))

                self.assertTrue(os.path.isfile('%s.m1' % path))
                self.assertFalse(os.path.isfile('%s.m2' % path))
                self.assertTrue(os.path.isfile('%s.m3' % path))
                self.assertFalse(os.path.isfile('%s.m4' % path))
                self.assertFalse(os.path.isfile('%s.m5' % path))

            with G.Session(graph, checkpoint_file=path, max_checkpoints=2) as sess:
                self.assertEqual(sess.next_checkpoint_index, 6)
                self.assertEqual(G.get_variable_values([a, b, c, d]), (13, 23, 33, 4))
                self.assertEqual((sess.memo['a'], sess.memo['b']), (101, 999))
