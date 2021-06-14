import threading
import time
import inspect


pc_data = threading.local()


class PerfCounter:
    def __init__(self, tag=None, show_time_to_last=False):
        self.start = time.perf_counter_ns()
        self.last_value = self.start
        self.display_counter = 0

        if tag is None:
            # If no tag given, default to the name of the calling func
            calling_frame = inspect.currentframe().f_back
            tag = calling_frame.f_code.co_name

        self.tag = tag
        if not hasattr(pc_data, 'depth'):
            pc_data.depth = 0
        pc_data.depth += 1

        self.show_time_to_last = show_time_to_last

    def __del__(self):
        pc_data.depth -= 1

    def measure(self):
        now = time.perf_counter_ns()
        cur_ms = (now - self.last_value) / 1000000
        self.last_value = now
        return cur_ms

    def display(self, name=None, show_time_to_last=False):
        if not name:
            name = '%d' % self.display_counter
            self.display_counter += 1

        now = time.perf_counter_ns()
        cur_ms = (now - self.start) / 1000000
        tag_str = '[%s] ' % self.tag if self.tag else ''
        if self.show_time_to_last:
            diff_str = ' (to previous %4.1f ms)' % ((now - self.last_value) / 1000000)
        else:
            diff_str = ''
        print('%s%s%6.1f ms%s: %s' % ((pc_data.depth - 1) * '  ', tag_str, cur_ms, diff_str, name))
        self.last_value = now
