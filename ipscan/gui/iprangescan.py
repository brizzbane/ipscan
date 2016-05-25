import logging
import os
import re
from collections import defaultdict
import functools
import json

import pika
from iptools import IpRange

from tornado.escape import json_decode

from PyQt5.uic import loadUi
from PyQt5.QtCore import Qt, QProcess, QFile, QIODevice, QFileSystemWatcher
from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QStandardItemModel, QStandardItem

from publishtorabbit import RabbitMQPublishThread


from ipscan.by_asn import IpAsnRangeDoc
from ipscan.config import configure_logging

configure_logging()
logger = logging.getLogger('masscan')
debug, info, warn, error, critical = logger.debug, logger.info, logger.warn, logger.error, logger.critical

scan_stats = re.compile(ur'(?P<kpps>\d{1,}.\d{1,})-kpps,\s+(?P<percent>\d{1,}.\d{1,})%.*(?P<remaining>\d{1,}:\d{1,}:\d{1,}).*found=(?P<found>\d{1,})')

class IpRangeScanGUI(QWidget):
    sequenceNumber = 1

    def __init__(self, mainwindow):
        self.mainwindow = mainwindow
        self.ipranges = defaultdict(dict)
        super(IpRangeScanGUI, self).__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.processed = 0
        self.isUntitled = True
        self.to_check = {}
        self.scanprocess = QProcess(self)
        loadUi('%s/ui/iprangescan.ui' % os.path.dirname(__file__), self)
        self.associateModels()
        self.setupViews()
        self.process = QProcess(self)
        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(100)
        self.publishthread = RabbitMQPublishThread('amqp://guest:guest@localhost:5672/%2F')
        self.publishthread.start()
        try:
            os.remove('/tmp/masscan_out.tmp')
            os.remove('/tmp/masscan_in.tmp')
        except OSError:
            pass
        self.outfile = {'tmp': QFile('/tmp/masscan_out.tmp'), 'pos': 0,
                        'incomplete_line': None, 'complete_line': None}
        self.outfile['tmp'].open(QIODevice.WriteOnly)
        self.outfile['tmp'].close()
        self.outfile['tmp'].open(QIODevice.ReadOnly)

    def setupViews(self):
        self.startScanButton.clicked.connect(functools.partial(self.start_scan))

    def associateModels(self):
        self.scan_ports = [1080, 82, 3128, 3129, 6088, 808, 8080, 8081, 8088, 8090, 8090, 8118, 8123, 8888, 9080,
                           9999, 3130, 8000, 8082, 9999]
        self.tableViewIpRangeModel = QStandardItemModel(0, len(self.scan_ports) + 1, self)
        self.tableViewIpRangeModel.setHeaderData(0, Qt.Horizontal, 'cidr')
        for i, port in enumerate(self.scan_ports):
            self.tableViewIpRangeModel.setHeaderData(i + 1, Qt.Horizontal, port)

        self.tableView.setModel(self.tableViewIpRangeModel)
        response = IpAsnRangeDoc().search().query('match', owner='amazon.com').execute()
        total_ip_count = 0
        for itm in response:
            for iprange in itm['ranges']:
                total_ip_count += iprange['ip_count']
                row = [QStandardItem(str(iprange['cidr']))]
                for _ in range(len(self.scan_ports)):
                    row.append(QStandardItem(str(0)))

                self.ipranges[IpRange(iprange['cidr'])] = row
                self.tableViewIpRangeModel.appendRow(self.ipranges[IpRange(iprange['cidr'])])


        self.ipCountLabel.setText('%s total ips.' % total_ip_count)
        self.progressBar.setMinimum(0)
        self.progressBar.setMaximum(total_ip_count)

    def start_scan(self):
        self.startScanButton.clicked.connect(functools.partial(self.stop_scan))
        self.run_masscan()

    def stop_scan(self):

        self.startScanButton.clicked.connect(functools.partial(self.start_scan))
        self.process.kill()
        self.startScanButton.setEnabled(False)
        self.startScanButton.setText('stopping..')
        self.process.waitForFinished(10)
        self.startScanButton.setText('start scan')
        self.startScanButton.setEnabled(True)
        self.spinBox.setEnabled(True)
        self.foundLabel.setText('0 port found.')
        self.currentRateLabel.setText('0 kpps.')
        self.remainingLabel.setText('not started.')
        self.progressBar.setValue(0)

    def run_masscan(self):
        self.process = QProcess(self)
        response = IpAsnRangeDoc().search().query('match', owner='amazon.com').execute()

        tmp_file = '/tmp/masscan_in.tmp'
        with open(tmp_file, 'a') as f:
            for iprange_doc in response:
                for range in iprange_doc.ranges:
                    debug('scan cidr %s' % range.cidr)
                    f.write('%s\n' % range.cidr)

        params = ['--output-format', 'json', '--output-filename', '/tmp/masscan_out.tmp', '--source-port', '60000',
                  '-p%s' % ''.join(str(self.scan_ports)[1:-1]),'--rate', '%s' % self.spinBox.value(), '-iL',
                  '/tmp/masscan_in.tmp']
        debug('masscan params' % params)

        self.process.start('masscan', params)

        self.startScanButton.setEnabled(False)
        self.startScanButton.setText('starting..')
        self.process.waitForStarted(1)
        self.startScanButton.setText('stop scan')
        self.startScanButton.setEnabled(True)
        self.spinBox.setEnabled(False)

        watch = QFileSystemWatcher(self)
        watch.addPath('/tmp/masscan_out.tmp')
        watch.fileChanged.connect(functools.partial(self.data_available))

        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardError.connect(functools.partial(self.stderrReady))
        self.process.stateChanged.connect(functools.partial(self.process_state_change))


    def process_state_change(self, state):
        debug('state %s' % state)

    def stderrReady(self):
        try:
            text = str(self.process.readAllStandardError())
            progress = scan_stats.search(text)
            if progress is not None:
                self.foundLabel.setText('%s port found.' % progress.groupdict()['found'])
                self.currentRateLabel.setText('%s kpps.' % progress.groupdict()['kpps'])
                self.remainingLabel.setText('%s h:m:s remaining.' % progress.groupdict()['remaining'])
                self.progressBar.setValue(float(progress.groupdict()['percent']))

                debug('%s ports found, %s kpps, %s remaining.' % (progress.groupdict()['found'],
                                                                        progress.groupdict()['kpps'],
                                                                        progress.groupdict()['remaining']))
        except KeyError:
            pass

    def data_available(self):

        self.outfile['tmp'].seek(self.outfile['pos'])
        self.outfile['complete_line'] = None
        while not self.outfile['tmp'].atEnd():
            line = str(self.outfile['tmp'].readLine()).rstrip()
            if self.outfile['incomplete_line'] is None and line[-6:] == '} ] },' and line.startswith('{   "ip":'):
                self.outfile['complete_line'] = line

            elif self.outfile['incomplete_line'] is not None:
                if line[-6:] == '} ] },':
                    self.outfile['complete_line'] = self.outfile['incomplete_line'] + line[-1]
                    self.outfile['incomplete_line'] = None
                else:
                    self.outfile['incomplete_line'] += line

            if self.outfile['complete_line'] is not None:
                line = json_decode(self.outfile['complete_line'][:-1])

                ip = line['ip']
                port = line['ports'][0]['port']

                message = {u'ip': ip,
                           u'port': port}

                properties = pika.BasicProperties(app_id='example-publisher',
                                                  content_type='application/json',
                                                  headers=message)

                self.publishthread._channel.basic_publish('proxies', 'proxies.port.proxycheck',
                                                                  json.dumps(message, ensure_ascii=False),
                                                                  properties)

                for (iprange, model) in self.ipranges.iteritems():
                    if line['ip'] in iprange:
                        current_value = self.tableViewIpRangeModel.takeItem(model[0].row(),
                                                                            self.scan_ports.index(port) + 1)
                        self.tableViewIpRangeModel.setItem(model[0].row(), self.scan_ports.index(port) + 1,
                                                           QStandardItem(str(int(current_value.text()) + 1)))
                        break

        self.outfile['pos'] = self.outfile['tmp'].pos()
