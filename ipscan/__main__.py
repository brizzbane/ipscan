import logging

from PyQt5.QtCore import QPoint, QSize, Qt, QSettings, QSignalMapper
from PyQt5.QtWidgets import QMainWindow, QApplication, QWidget, QAction, QMdiArea
from PyQt5.QtGui import QKeySequence

from config import configure_logging
from gui.iprangescan import IpRangeScanGUI

configure_logging()
logger = logging.getLogger('mainwindow')
debug, info, warn, error, critical = logger.debug, logger.info, logger.warn, logger.error, logger.critical


class NetMainWindow(QMainWindow):
    def __init__(self):
        super(NetMainWindow, self).__init__()
        self.mdiArea = QMdiArea()
        self.mdiArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.mdiArea.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setCentralWidget(self.mdiArea)
        self.windowMapper = QSignalMapper(self)
        self.windowMapper.mapped[QWidget].connect(self.setActiveSubWindow)
        self.createActions()
        self.createMenus()
        self.createStatusBar()
        self.readSettings()
        self.setWindowTitle('Net')

    def newScan(self):
        child = IpRangeScanGUI(self)
        self.mdiArea.addSubWindow(child)
        child.show()
        return child

    def createActions(self):
        self.newIPScanAct = QAction("&Start a Scan", self, shortcut=QKeySequence.New, statusTip="Start a Scan",
                                    triggered=self.newScan)

    def createMenus(self):
        self.serverMenu = self.menuBar().addMenu('&IP Scan')
        self.serverMenu.addAction(self.newIPScanAct)

    def createStatusBar(self):
        self.statusBar().showMessage("Ready")

    def readSettings(self):
        settings = QSettings('Trolltech', 'MDI Example')
        pos = settings.value('pos', QPoint(200, 200))
        size = settings.value('size', QSize(500, 500))
        self.move(pos)
        self.resize(size)

    def setActiveSubWindow(self, window):
        if window:
            self.mdiArea.setActiveSubWindow(window)


if __name__ == '__main__':
    import sys

    app = QApplication(sys.argv)
    mainWin = NetMainWindow()
    mainWin.show()
    sys.exit(app.exec_())
