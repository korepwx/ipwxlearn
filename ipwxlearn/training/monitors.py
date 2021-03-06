# -*- coding: utf-8 -*-
from __future__ import absolute_import

import math
import time
from datetime import datetime, timedelta

import numpy as np
import six

from ipwxlearn.training.dataflow import DataFlow, OneShotDataFlow, TestingBatchDataFlow
from ipwxlearn.utils.io import write_string
from ipwxlearn.utils.misc import ensure_list_sealed

__all__ = [
    'Monitor',
    'MonitorChain',
    'ValidationMonitor',
    'EveryFewStepMonitor',
    'CheckpointMonitor',
    'SummaryMonitor',
    'TrainingLossMonitor'
]


class Monitor(object):
    """Base monitor class that watches training process."""

    def start_training(self, G, batch_size, steps_in_epoch, max_steps, initial_step=0):
        """
        Tell the monitor that a training loop will start.

        :param G: Tensor backend adapter.
        :param batch_size: Size of each step (mini-batch).
        :param steps_in_epoch: Estimated number of steps in one epoch.
        :param max_steps: Hard limit of total steps.
        :param initial_step: Initial step index for this training.  An initial_step > 0 indicates
                             a recovered training from checkpoint file.
        """

    def end_training(self):
        """Tell the monitor that a training loss has been completed."""

    def start_epoch(self, epoch):
        """
        Tell the monitor that a training epoch will start.
        :param epoch: Index of the epoch, starting from 0.
        """

    def end_epoch(self, epoch, avg_loss):
        """
        Tell the monitor that a training epoch has been completed.

        :param epoch: Index of the epoch, starting from 0.
        :param avg_loss: Average training loss of all the mini-batches in this epoch.
        """

    def start_step(self, step):
        """
        Tell the monitor that a training step (mini-batch) will start.

        :param step: Index of the step, starting from 0.
                     It is also the total number of mini-batches have ever been performed.
        """

    def end_step(self, step, loss):
        """
        Tell the monitor that a training step (mini-batch) has been completed.
        :param step: Index of the step, starting from 0.
        :param loss: Training loss of this step.
        """

    @property
    def is_inducing_stopping(self):
        """Whether or not this monitor is inducing early-stopping?"""
        return False


class MonitorChain(Monitor):
    """
    Chain of monitors, to aggregate multiple monitors into one.

    Methods of the monitors in this chain would be called one by one, in determined order.
    If any one of the monitors is inducing early-stopping, then the whole chain would do so.
    """

    def __init__(self, monitors):
        self.monitors = ensure_list_sealed(monitors)

    def start_training(self, G, batch_size, steps_in_epoch, max_steps, initial_step=0):
        for m in self.monitors:
            m.start_training(G, batch_size, steps_in_epoch, max_steps, initial_step)

    def end_training(self):
        for m in self.monitors:
            m.end_training()

    def start_epoch(self, epoch):
        for m in self.monitors:
            m.start_epoch(epoch)

    def end_epoch(self, epoch, avg_loss):
        for m in self.monitors:
            m.end_epoch(epoch, avg_loss)

    def start_step(self, step):
        for m in self.monitors:
            m.start_step(step)

    def end_step(self, step, loss):
        for m in self.monitors:
            m.end_step(step, loss)

    @property
    def is_inducing_stopping(self):
        return any(m.is_inducing_stopping for m in self.monitors)


class ValidationMonitor(Monitor):
    """
    Monitor that performs validation and early-stopping.

    This monitor computes the loss on validation set every few steps, and use the validation loss
    to determine whether or not to accept the current set of parameters.

    :param valid_fn: Callable function to perform a validation pass.
                     This function should either return a scalar which indicates the training loss,
                     or return a tuple which contains not only the training loss, but also the summary object
                     for the loss.
    :param valid_data: Numpy array, a list of numpy arrays, or a DataFlow object as the validation data.
                       If it is a DataFlow, it must yield exactly one batch of data for validation in each epoch.
    :param params: List of parameters that should be regularized by early-stopping.
                   If not specified, will select all the trainable variables in current graph.
                   To disable early-stopping on parameters, you may pass an empty list or tuple.
    :param steps: Perform validation every this number of steps.
                  If not specified, will use (valid_data_count / training_batch_size).
    :param stopping_steps: If not None, will induce early stopping if no improvement has been achieved
                           after this number of steps.
    :param validation_batch: Batch size for validation.  If not specified, will compute validation loss in one batch.
    :param validation_loss_name: Alternative name of the validation loss (e.g., "validation_error")
    :param log_file: Print the loss to this file.
    :param summary_writer: If specified, will try to output the summary of training loss.
    """

    def __init__(self, valid_fn, valid_data, params=None, steps=None, stopping_steps=None, validation_batch=None,
                 validation_loss_name=None, log_file=None, summary_writer=None):
        self._valid_fn = valid_fn
        if not isinstance(valid_data, DataFlow):
            if validation_batch is not None:
                valid_data = TestingBatchDataFlow(ensure_list_sealed(valid_data), validation_batch)
            else:
                valid_data = OneShotDataFlow(ensure_list_sealed(valid_data))
        self._valid_data = valid_data
        self._params = params
        self._steps = steps
        self._stopping_steps = stopping_steps
        self._validation_batch = validation_batch
        self._validation_loss_name = validation_loss_name
        self._log_file = log_file
        self._summary_writer = summary_writer

        # reference to the backend
        self._G = None
        # loss variable and loss summary operation.
        self._loss_var = self._summary_op = None

        # sum of the training loss since last report
        self._train_loss_sum = None
        # number of training loss since last report
        self._train_loss_num = None

        # start time stamp.
        self._start_time_stamp = None
        # this monitor will do validation every this number of steps (guaranteed not None after training started).
        self._actual_steps = None
        # number of steps remaining before performing another validation.
        self._remain_steps = None
        # number of steps remaining before inducing early stopping.
        self._remain_stopping_steps = None

        # the session memo dict
        self._memo = None

    def start_training(self, G, batch_size, steps_in_epoch, max_steps, initial_step=0):
        self._G = G

        # in case the validation function does not return summary, or we perform validation in mini-batches,
        # we would have to construct the loss summary manually.
        from ipwxlearn import glue
        validation_loss_name = self._validation_loss_name or 'validation_loss'
        self._loss_var = G.make_placeholder('validation_loss', shape=(), dtype=glue.config.floatX)
        self._summary_op = G.summary.scalar_summary(validation_loss_name, self._loss_var)

        # clear the training loss sum
        self._train_loss_sum = self._train_loss_num = 0

        # determine the step interval.
        if self._steps is None:
            num_examples = self._valid_data.num_examples
            # automatically determine the step interval, such that:
            #
            # 1. At least the same number of training data is used before using the validation data.
            # 2. Validation step should no less than min(100, max_steps * 0.1)
            # 3. A multiple of 10, 100 or 1000, etc, according to the step-interval selected from previous rule.
            actual_steps = (num_examples + batch_size - 1) // batch_size
            actual_steps = max(min(100, int(max_steps * 0.1)), actual_steps)
            ten_base = 10 ** int(math.log(actual_steps, 10))
            self._actual_steps = ((actual_steps + ten_base - 1) // ten_base) * ten_base
        else:
            self._actual_steps = self._steps

        # reset the remaining counters.
        self._remain_steps = self._actual_steps - initial_step % self._actual_steps
        if self._stopping_steps is not None:
            self._remain_stopping_steps = max(self._stopping_steps, self._actual_steps) - initial_step

        # resume the previous training
        self._memo = G.current_session().memo.with_prefix(self.__class__.__name__)

        # set the start time stamp
        self._start_time_stamp = time.time()
        if self._log_file:
            time_str = datetime.strftime(datetime.fromtimestamp(self._start_time_stamp), '%Y-%m-%d %H:%M:%S')
            if initial_step > 0:
                write_string(self._log_file, 'Resume training at %s, max steps is %s, last step is %s.\n' %
                                             (time_str, max_steps, initial_step))
            else:
                write_string(self._log_file, 'Start training at %s, max steps is %s.\n' % (time_str, max_steps))

    def _do_validation(self, step, train_loss):
        """Perform the validation and early-stopping."""
        G = self._G
        start_valid_time = time.time()

        # compute the validation loss.
        valid_result = []
        valid_weights = []

        for args in self._valid_data.iter_epoch():
            valid_weights.append(len(args[0]))
            valid_result.append(self._valid_fn(*args))

        if len(valid_result) == 0:
            raise RuntimeError('No validation data.')
        elif len(valid_result) == 1:
            if isinstance(valid_result[0], (tuple, list)):
                loss, summary = valid_result[0]
            else:
                loss = valid_result[0]
                summary = self._summary_op
        else:
            # we've performed validation in mini-batches, thus we must compose the summary ourselves.
            weights = np.array(valid_weights) / np.sum(valid_weights).astype(np.float32)
            losses = np.array([v[0] if isinstance(v, (tuple, list)) else v for v in valid_result])
            loss = np.sum(weights * losses)
            summary = self._summary_op

        if self._summary_writer is not None and summary is not None and step is not None:
            self._summary_writer.write(summary, global_step=step, givens={self._loss_var: loss})

        # do early-stopping.
        params = self._params if self._params is not None else G.current_graph().get_variables(trainable=True)
        session = G.current_session()
        best_params_updated = False
        if loss < self._memo.get('best_valid_loss', np.inf):
            best_params_updated = True
            # record the currently found best parameter.
            self._memo['best_valid_loss'] = loss
            self._memo['best_params'] = {
                session.graph.get_variable_info(k).full_name: v
                for k, v in six.iteritems(session.get_variable_values_dict(params))
            }
            # set the flag that we've got a better parameter, so do not induce early stopping.
            if self._stopping_steps is not None:
                self._remain_stopping_steps = self._stopping_steps

        # report the loss if required
        if step is not None and self._log_file:
            best_mark = ' (*)' if (best_params_updated and params) else ''
            time_offset = str(timedelta(seconds=time.time() - self._start_time_stamp))
            if '.' in time_offset:
                time_offset = time_offset[: time_offset.find('.')]
            valid_time_usage = time.time() - start_valid_time
            msg = ('Step %d: at %s, average train loss %.6f, valid loss %.6f; validated in %.2f secs.%s\n' %
                   (step, time_offset, train_loss, loss, valid_time_usage, best_mark))
            write_string(self._log_file, msg)
            self._log_file.flush()

    def end_step(self, step, loss):
        # sum up training loss
        self._train_loss_sum += loss
        self._train_loss_num += 1

        # do validation if necessary.
        if self._remain_steps <= 0:
            train_loss = self._train_loss_sum / float(self._train_loss_num)
            self._do_validation(step, train_loss)
            self._remain_steps = self._actual_steps
            self._train_loss_sum = self._train_loss_num = 0

        # decrease the counter.
        self._remain_steps -= 1
        if self._remain_stopping_steps is not None:
            self._remain_stopping_steps -= 1

    def end_training(self):
        from ipwxlearn.glue import current_session
        # perform the final validation if there's some more training since the last validation.
        if self._remain_steps < self._actual_steps:
            self._do_validation(None, None)
        # restore the best ever params.
        best_params = self._memo.get('best_params', None)
        if best_params is not None:
            session = current_session()
            session.set_variable_values({
                session.graph.get_variable(k): v for k, v in six.iteritems(best_params)
            })
        # and finally, we should clear the recorded best params in the session.
        self._memo['best_params'] = self._memo['best_valid_loss'] = None

    @property
    def is_inducing_stopping(self):
        return self._remain_stopping_steps is not None and self._remain_stopping_steps <= 0


class EveryFewStepMonitor(Monitor):
    """
    Monitor to run every few steps or duration.

    :param seconds: Save session checkpoint every this number of seconds.
    :param steps: Save session checkpoint every this number of steps.
    """

    def __init__(self, seconds=None, steps=None):
        if seconds is None and steps is None:
            raise ValueError('At least either "seconds" or "steps" should be specified.')

        self._seconds = seconds
        self._steps = steps

        # last checkpoint time and step
        self._last_chk_time = None
        self._last_chk_step = None

    def start_training(self, G, batch_size, steps_in_epoch, max_steps, initial_step=0):
        self._last_chk_time = time.time()
        self._last_chk_step = 0

    def _every_few_steps(self, step, loss, now_time):
        """Run monitor after given step."""
        raise NotImplementedError()

    def end_step(self, step, loss):
        if (self._steps is not None and (step - self._last_chk_step) >= self._steps) or \
                (self._seconds is not None and (time.time() - self._last_chk_time) >= self._seconds):
            now_time = time.time()
            self._every_few_steps(step, loss, now_time)
            self._last_chk_time = time.time()
            self._last_chk_step = step


class CheckpointMonitor(EveryFewStepMonitor):
    """
    Monitor to save session checkpoints every few steps or duration.

    :param seconds: Save session checkpoint every this number of seconds.
    :param steps: Save session checkpoint every this number of steps.
    :param log_file: Print the message that checkpoint has been saved to this file.
    """

    def __init__(self, seconds=None, steps=None, log_file=None):
        super(CheckpointMonitor, self).__init__(seconds, steps)
        self._log_file = log_file

    def _every_few_steps(self, step, loss, now_time):
        from ipwxlearn.glue import current_session
        current_session().checkpoint()
        if self._log_file:
            time_str = datetime.strftime(datetime.fromtimestamp(now_time), '%Y-%m-%d %H:%M:%S')
            write_string(self._log_file, 'Checkpoint saved at step %d, %s.\n' % (step, time_str))
            self._log_file.flush()


class SummaryMonitor(EveryFewStepMonitor):
    """
    Monitor to save summaries every few steps or duration.

    :param writer: Backend summary writer.
    :param summary: Compiled backend summary object.
    :param seconds: Save session checkpoint every this number of seconds.
    :param steps: Save session checkpoint every this number of steps.
    """

    def __init__(self, writer, summary, seconds=None, steps=None):
        super(SummaryMonitor, self).__init__(seconds, steps)
        self._writer = writer
        self._summary = summary

    def _every_few_steps(self, step, loss, now_time):
        self._writer.write(self._summary, step)


class TrainingLossMonitor(EveryFewStepMonitor):
    """
    Monitor to print the average training loss every few steps or duration.

    :param log_file: Print the message that checkpoint has been saved to this file.
    :param seconds: Save session checkpoint every this number of seconds.
    :param steps: Save session checkpoint every this number of steps.
    """

    def __init__(self, log_file, seconds=None, steps=None):
        super(TrainingLossMonitor, self).__init__(seconds, steps)
        self._log_file = log_file
        self._sum_loss = self._num_steps = self._start_time_stamp = None

    def start_training(self, G, batch_size, steps_in_epoch, max_steps, initial_step=0):
        self._sum_loss = self._num_steps = 0
        self._start_time_stamp = time.time()
        super(TrainingLossMonitor, self).start_training(batch_size, steps_in_epoch, max_steps, initial_step)

    def end_step(self, step, loss):
        self._sum_loss += loss
        self._num_steps += 1
        super(TrainingLossMonitor, self).end_step(step, loss)

    @property
    def avg_loss(self):
        return self._sum_loss / float(self._num_steps)

    def _every_few_steps(self, step, loss, now_time):
        if self._num_steps > 0 and self._log_file:
            time_offset = str(timedelta(seconds=time.time() - self._start_time_stamp))
            if '.' in time_offset:
                time_offset = time_offset[: time_offset.find('.')]
            msg = ('Step %d: at %s, average train loss %.6f.\n' % (step, time_offset, self.avg_loss))
            write_string(self._log_file, msg)
            self._log_file.flush()
        self._num_steps = self._sum_loss = 0
