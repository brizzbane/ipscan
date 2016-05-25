"""Use pika with the libev IOLoop via pyev"""
import pyuv
import signal
import array
import logging
import warnings

import functools
from collections import deque

from pika.adapters.base_connection import BaseConnection

LOGGER = logging.getLogger(__name__)

global_sigint_watcher, global_sigterm_watcher = None, None

class LibuvConnection(BaseConnection):
    """The LibevConnection runs on the libev IOLoop. If you're running the
    connection in a web app, make sure you set stop_ioloop_on_close to False,
    which is the default behavior for this adapter, otherwise the web app
    will stop taking requests.

    You should be familiar with pyev and libev to use this adapter, esp.
    with regard to the use of libev ioloops.

    If an on_signal_callback method is provided, the adapter creates signal
    watchers the first time; subsequent instantiations with a provided method
    reuse the same watchers but will call the new method upon receiving a
    signal. See pyev/libev signal handling to understand why this is done.

    :param pika.connection.Parameters parameters: Connection parameters
    :param on_open_callback: The method to call when the connection is open
    :type on_open_callback: method
    :param on_open_error_callback: Method to call if the connection can't
                                   be opened
    :type on_open_error_callback: method
    :param bool stop_ioloop_on_close: Call ioloop.stop() if disconnected
    :param custom_ioloop: Override using the default_loop in libev
    :param on_signal_callback: Method to call if SIGINT or SIGTERM occur
    :type on_signal_callback: method

    """
    WARN_ABOUT_IOLOOP = True

    # use static arrays to translate masks between pika and libev
    _PIKA_TO_LIBUV_ARRAY = array.array('i', [0] * (
        (BaseConnection.READ | BaseConnection.WRITE | BaseConnection.ERROR) + 1
    ))

    _PIKA_TO_LIBUV_ARRAY[BaseConnection.READ] = pyuv.UV_READABLE
    _PIKA_TO_LIBUV_ARRAY[BaseConnection.WRITE] = pyuv.UV_WRITABLE

    _PIKA_TO_LIBUV_ARRAY[BaseConnection.READ |
                         BaseConnection.WRITE] = pyuv.UV_READABLE | pyuv.UV_WRITABLE

    _PIKA_TO_LIBUV_ARRAY[BaseConnection.READ |
                         BaseConnection.ERROR] = pyuv.UV_READABLE

    _PIKA_TO_LIBUV_ARRAY[BaseConnection.WRITE |
                         BaseConnection.ERROR] = pyuv.UV_WRITABLE

    _PIKA_TO_LIBUV_ARRAY[BaseConnection.READ | BaseConnection.WRITE |
                         BaseConnection.ERROR] = pyuv.UV_READABLE | pyuv.UV_WRITABLE

    _LIBUV_TO_PIKA_ARRAY = array.array('i', [0] *
                                       ((pyuv.UV_READABLE | pyuv.UV_WRITABLE) + 1))

    _LIBUV_TO_PIKA_ARRAY[pyuv.UV_READABLE] = BaseConnection.READ
    _LIBUV_TO_PIKA_ARRAY[pyuv.UV_WRITABLE] = BaseConnection.WRITE

    _LIBUV_TO_PIKA_ARRAY[pyuv.UV_READABLE | pyuv.UV_WRITABLE] = \
        BaseConnection.READ | BaseConnection.WRITE

    def __init__(self,
                 parameters=None,
                 on_open_callback=None,
                 on_open_error_callback=None,
                 on_close_callback=None,
                 stop_ioloop_on_close=False,
                 custom_ioloop=None,
                 on_signal_callback=None):
        """Create a new instance of the LibevConnection class, connecting
        to RabbitMQ automatically

        :param pika.connection.Parameters parameters: Connection parameters
        :param on_open_callback: The method to call when the connection is open
        :type on_open_callback: method
        :param on_open_error_callback: Method to call if the connection cannot
                                       be opened
        :type on_open_error_callback: method
        :param bool stop_ioloop_on_close: Call ioloop.stop() if disconnected
        :param custom_ioloop: Override using the default IOLoop in libev
        :param on_signal_callback: Method to call if SIGINT or SIGTERM occur
        :type on_signal_callback: method

        """
        if custom_ioloop:
            self.ioloop = custom_ioloop
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
              #  self.ioloop = gruvi.get_hub().loop
                self.ioloop = pyuv.Loop.default_loop()

        self.async = None
        self._on_signal_callback = on_signal_callback
        self._io_watcher = None
        self._active_timers = {}
        self._stopped_timers = deque()

        super(LibuvConnection, self).__init__(parameters, on_open_callback,
                                              on_open_error_callback,
                                              on_close_callback, self.ioloop,
                                              stop_ioloop_on_close)

    def _adapter_connect(self):
        """Connect to the remote socket, adding the socket to the IOLoop if
        connected

        :rtype: bool

        """
        LOGGER.debug('init io and signal watchers if any')
        # reuse existing signal watchers, can only be declared for 1 ioloop
        global global_sigint_watcher, global_sigterm_watcher
        error = super(LibuvConnection, self)._adapter_connect()

        if not error:
            if self._on_signal_callback and not global_sigterm_watcher:
                global_sigint_watcher = pyuv.Signal(self.ioloop)
            if self._on_signal_callback and not global_sigint_watcher:
                global_sigterm_watcher = pyuv.Signal(self.ioloop)

            if not self._io_watcher:
                self._io_watcher = pyuv.Poll(self.ioloop, self.socket.fileno())
                self._io_watcher.fd = self.socket.fileno()

            self.async = pyuv.Async(self.ioloop, self._noop_callable())
            self.async.send()
            if self._on_signal_callback:
                global_sigterm_watcher.start(signal.SIGTERM, self._handle_sigterm)
                global_sigint_watcher.start(signal.SIGINT, self._handle_sigint)
            self._io_watcher.start(self._PIKA_TO_LIBUV_ARRAY[self.event_state], self._handle_events)


        return error

    def _noop_callable(self, *args, **kwargs):
        pass

    def _init_connection_state(self):
        """Initialize or reset all of our internal state variables for a given
        connection. If we disconnect and reconnect, all of our state needs to
        be wiped.

        """
        active_timers = list(self._active_timers.keys())
        for timer in active_timers:
            self.remove_timeout(timer)
        if global_sigint_watcher:
            global_sigint_watcher.stop()
        if global_sigterm_watcher:
            global_sigterm_watcher.stop()
        if self._io_watcher:
            self._io_watcher.stop()
        super(LibuvConnection, self)._init_connection_state()

    def _handle_sigint(self, signal_watcher, libev_events):
        """If an on_signal_callback has been defined, call it returning the
        string 'SIGINT'.

        """
        LOGGER.debug('SIGINT')
        self._on_signal_callback('SIGINT')

    def _handle_sigterm(self, signal_watcher, libev_events):
        """If an on_signal_callback has been defined, call it returning the
        string 'SIGTERM'.

        """
        LOGGER.debug('SIGTERM')
        self._on_signal_callback('SIGTERM')

    def _handle_events(self, handle, events, error=None, **kwargs):
        """Handle IO events by efficiently translating to BaseConnection
        events and calling super.

        """
        super(LibuvConnection,
              self)._handle_events(handle.fd,
                                   self._LIBUV_TO_PIKA_ARRAY[events],
                                   **kwargs)

    def _reset_io_watcher(self):
        """Reset the IO watcher; retry as necessary

        """
        self._io_watcher.stop()

        retries = 0
        while True:
            try:
                self._io_watcher.set(
                    self._io_watcher.fd,
                    self._PIKA_TO_LIBUV_ARRAY[self.event_state])

                break
            except:  # sometimes the stop() doesn't complete in time
                if retries > 5: raise
                self._io_watcher.stop()  # so try it again
                retries += 1

        self._io_watcher.start()

    def _manage_event_state(self):
        """Manage the bitmask for reading/writing/error which is used by the
        io/event handler to specify when there is an event such as a read or
        write.

        """
        if self.outbound_buffer:
            if not self.event_state & self.WRITE:
                self.event_state |= self.WRITE
                self._reset_io_watcher()
        elif self.event_state & self.WRITE:
            self.event_state = self.base_events
            self._reset_io_watcher()

    def _timer_callback(self, timer, libev_events):
        """Manage timer callbacks indirectly."""
        if timer in self._active_timers:
            (callback_method, callback_timeout,
             kwargs) = self._active_timers[timer]

            if callback_timeout:
                callback_method(timeout=timer, **kwargs)
            else:
                callback_method(**kwargs)

            self.remove_timeout(timer)
        else:
            LOGGER.warning('Timer callback_method not found')

    def _get_timer(self, deadline):
        """Get a timer from the pool or allocate a new one."""
        if self._stopped_timers:
            timer = self._stopped_timers.pop()
            timer.set(deadline, 0.0)
        else:
            timer = pyuv.Timer(self.ioloop)
            timer.start(self._timer_callback, deadline, 0.0)

        return timer

    def add_timeout(self, deadline, callback_method,
                    callback_timeout=False, **callback_kwargs):
        """Add the callback_method indirectly to the IOLoop timer to fire
         after deadline seconds. Returns the timer handle.

        :param int deadline: The number of seconds to wait to call callback
        :param method callback_method: The callback method
        :param callback_timeout: Whether timeout kwarg is passed on callback
        :type callback_timeout: boolean
        :param kwargs callback_kwargs: additional kwargs to pass on callback
        :rtype: timer instance handle.

        """
        LOGGER.debug('deadline: {0}'.format(deadline))
        #timer = self._get_timer(deadline)
        timer = pyuv.Timer(self.ioloop)


        self._active_timers[timer] = (functools.partial(callback_method, callback_kwargs), callback_timeout)

       # timer.start(functools.partial(callback_method, callback_kwargs), deadline, 0)
        timer.start(callback_method, deadline, 0)
        return timer

    def remove_timeout(self, timer):
        """Remove the timer from the IOLoop using the handle returned from
        add_timeout.

        param: timer instance handle

        """
        LOGGER.debug('stop')
        self._active_timers.pop(timer, None)
        timer.stop()
        self._stopped_timers.append(timer)

    def _create_and_connect_to_socket(self, sock_addr_tuple):
        """Call super and then set the socket to nonblocking."""
        result = super(LibuvConnection,
                       self)._create_and_connect_to_socket(sock_addr_tuple)
        if result:
            self.socket.setblocking(0)
        return result
