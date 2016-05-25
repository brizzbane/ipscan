import logging
import pika
import pyuv

from PyQt5.QtCore import QMutex, QWaitCondition, QThread

from ipscan.config import configure_logging
from ipscan.pika_adapter import LibuvConnection

configure_logging()
curl_log = logging.getLogger('RabbitMQ')
debug, info, warn, error, critical = curl_log.debug, curl_log.info, curl_log.warn, curl_log.error, curl_log.critical

class RabbitMQPublishThread(QThread):

    ROUTING_KEY = 'ports.to_check'
    DURABLE = False
    QOS = 1

    def __init__(self, amqp_url, parent=None):
        super(RabbitMQPublishThread, self).__init__()
        self.ioloop = pyuv.Loop.default_loop()
        self.rabbitmq = {}
        self._connection = None
        self._channel = None
        self._closing = False
        self._consumer_tag = None
        self._url = amqp_url

        self.mutex = QMutex()
        self.condition = QWaitCondition()
        self.restart = False
        self.abort = False

    def __del__(self):
        self.mutex.lock()
        self.abort = True
        self.condition.wakeOne()
        self.mutex.unlock()


    def run(self):
        while True:
            self.mutex.lock()
            self.mutex.unlock()
            self._connection = self.connect()
            self._connection.ioloop.run()
            self.mutex.lock()
            if not self.restart:
                self.condition.wait(self.mutex)
            self.restart = False
            self.mutex.unlock()


    def connect(self):


        def on_connection_closed(connection, reply_code, reply_text):

            self._channel = None
            if self._closing:
                self._connection.ioloop.stop()
            else:
                warn('Connection closed, reopening in 5 seconds: (%s) %s',
                               reply_code, reply_text)
            self._connection.add_timeout(5, self.reconnect)

        def on_connection_open(unused_connection):
            info('Connection opened')
            self._connection.add_on_close_callback(on_connection_closed)
            self._connection.channel(on_open_callback=self.on_channel_open)


        info('Connecting to %s', self._url)
        return LibuvConnection(pika.URLParameters(self._url),
                                            on_connection_open,
                                            stop_ioloop_on_close=False)
    def reconnect(self):
        self._connection.ioloop.stop()
        if not self._closing:
            self._connection = self.connect()
            self._connection.ioloop.run()


    def on_channel_open(self, channel):

        def on_bindok(unused_frame):
            info('Queue bound')

        def on_queue_declareok(method_frame):
            self._channel.queue_bind(on_bindok, 'proxies.port.proxycheck',
                                     'proxies', 'proxies.port.proxycheck')

        def on_exchange_declareok(unused_frame):
            self._channel.queue_declare(on_queue_declareok, 'proxies.port.proxycheck')

        def setup_exchange(exchange_name):
            info('Declaring exchange %s', exchange_name)
            self._channel.exchange_declare(on_exchange_declareok,
                                           exchange_name,
                                           'direct')
        info('Channel opened')
        self._channel = channel
        self._channel.basic_qos(prefetch_count=self.QOS)
        self.add_on_channel_close_callback()
        setup_exchange('proxies')

    def add_on_channel_close_callback(self):
        info('Adding channel close callback')
        self._channel.add_on_close_callback(self.on_channel_closed)

    def on_channel_closed(self, channel, reply_code, reply_text):
        warn('Channel %i was closed: (%s) %s',
                       channel, reply_code, reply_text)
        self._connection.close()



       # self.start_consuming()

    def start_consuming(self):
        info('Issuing consumer related RPC commands')
        self.add_on_cancel_callback()
        self._consumer_tag = self._channel.basic_consume(self.on_message,
                                                         self.QUEUE)

    def add_on_cancel_callback(self):
        info('Adding consumer cancellation callback')
        self._channel.add_on_cancel_callback(self.on_consumer_cancelled)

    def on_consumer_cancelled(self, method_frame):
        info('Consumer was cancelled remotely, shutting down: %r',
                    method_frame)
        if self._channel:
            self._channel.close()

    def on_message(self, unused_channel, basic_deliver, properties, body):
        info('Received message # %s from %s: %s',
                    basic_deliver.delivery_tag, properties.app_id, body)



    def acknowledge_message(self, delivery_tag):
        info('Acknowledging message %s', delivery_tag)
        self._channel.basic_ack(delivery_tag)

    def stop_consuming(self):
        if self._channel:
            info('Sending a Basic.Cancel RPC command to RabbitMQ')
            self._channel.basic_cancel(self.on_cancelok, self._consumer_tag)

    def on_cancelok(self, unused_frame):
        info('RabbitMQ acknowledged the cancellation of the consumer')
        self.close_channel()

    def close_channel(self):
        info('Closing the channel')
        self._channel.close()

    def stop(self):
        info('Stopping')
        self._closing = True
        self.stop_consuming()
        self._connection.ioloop.start()
        info('Stopped')

    def close_connection(self):
        """This method closes the connection to RabbitMQ."""
        info('Closing connection')
        self._connection.close()