#! /usr/bin/env python3

"""
Author: Nick Fan
Date: February 2023
Description: Liquid Rocket Project Launch Control GUI prototype.
"""

import re
import sys

import numpy as np
from pyqtgraph import PlotWidget, mkPen, setConfigOption
from PyQt6.QtCore import QDateTime, Qt, QThread, QTimer, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QWidget,
)

from utils import *

# CONSTANTS -----------------------------------------------------------------|

MIN_SIZE = 630
ICON_PATH = "./src/rocketIcon.png"
if LIGHT:
    WIRE_DIAGRAM = "./src/wireDiagBlack.svg"
else:
    WIRE_DIAGRAM = "./src/wireDiagWhite.svg"

LAUNCH_STATES = ("IDLE", "SYSTEM CHECKS", "HIGH PRESSURE", "TANK HIGH PRESSURE", "FIRE")
LEAK_ACCEPT_RATE = "1 PSI / Min"

# Dynamic Labels Map
DIAGRAM_LABEL = "D_Label"
STATUS_LABEL = "Status"
CURR_STATE = "StateDisplay"
ABORT = "Abort"
SV = "SV"
PT = "PT"
DT = "Decay Test"
DT_STOP = "Stop Test"
DT_ITERS = 5
DT_ITER_LEN_SECONDS = 60

# Button Map
PROCEED = "\nADVANCE STAGE\n"
PREVIOUS = "\nRETURN TO LAST\n"
# IGNITION_FAILURE = "IGNITION FAIL"
# OVERPRESSURE = "OVERPRESSURE"
ABORT_MISSION = "ABORT MISSION (CLOSE MVs)"
SETUP_SER = "SERIAL SETTINGS"
SER_TOGGLE = "START SERIAL"
SER_ON = "START SERIAL"
SER_OFF = "STOP SERIAL"
SERIAL_SEND = "Send"
LOCK = "Unlock"
IGNITE = "IGNITE"
MAINVALVES = "MVs"

# Pins
# PIN MAP ##########################
PIN_MAP = [1, 2, 3, 4, 5, 6, 8, 7 , 9]#range(1, 10)#[9, 5, 7, 6, 2, 1, 8, 3, 4]
PIN_READ_MAP = {str(x): str(i + 1) for i, x in enumerate(PIN_MAP)}

# ANALOG (PT) MAP ##################
# ANALOG_MAP[1] == num of second analog reading == 3 in [1, 3, 2, 4]
ANALOG_MAP = [1, 3, 2, 4, 5, 6, 7, 8, 9]
ACTIVE_PTS = ["PT1", "PT2", "PT3"]

# SV names
SV_NAMES = {"SV1": "CVENT",
            "SV2": "N/A", #"CFILL",
            "SV3": "HIGH PRESS", 
            "SV4": "FUEL VENT",
            "SV5": "N/A", #"NITRO FILL"
            "SV6": "NITRO VENT",
            "SV7": "P VENT",
            "SV8": "NITRO MAIN",
            "SV9": "FUEL MAIN"}

# COMMAND CHARS ####################
ABORT_CMD = "a"
MAINVALVE_CMD = "m"
IGNITE_CMD = "i"

###################################
COMMAND_LEN = 8
MSG_PAD = lambda x: x + "0" * (8 - len(x))
DISP_FORMAT = lambda name, val: f"{name}:{val}"
PRESSURE_TAG = ""  # no tag rn
PRESSURE_SEP = ", "
VALVE_TAG = "PS"
#VALVE_SEP = " "


# Safety Thresholds
# PTs
SAFE_PRESS = range(-1000, 400)
MID_PRESS = range(401, 501)
# Avg PSI
#SAFE_AVG_PRESS = range()
#MID_AVG_PRESS = range()

# Files
DATE = QDateTime.currentDateTime().toString("MM-dd-yy")
START_TIME = QDateTime.currentDateTime().toString("MM-dd-yy-hh-mm")
DATA_LOG_FILE = f"./log/data/{DATE}.txt"
SYS_LOG_FILE = f"./log/sys/{DATE}.txt"

# Graphs
WIDGET = "widget"
GRAPH = "graph"
TIME = "time"
DATA = "data"
TIMESTAMP = "tstamp"

FUEL_GRAPH = "Fuel: PSI vs Seconds"
OX_GRAPH = "Ox: PSI vs Seconds"
PSI_CHANGE = "PSI/MIN"
PSI_PER_MIN = lambda num: f"{PSI_CHANGE}: %.1f" % num
ROLLING_AVG_SAMPLE_SIZE = 12

PSI_SAMPLE_SIZE = 600
DISPLAYED_SAMPLE_SIZE = 100


# MAIN WINDOW ---------------------------------------------------------------|


class RocketDisplayWindow(QMainWindow):
    """Main Rocket Control Window."""

    graphData = pyqtSignal(str, int)

    def __init__(self) -> None:
        """Constructs new Rocket Display Window."""
        super().__init__()

        # launch state
        self.mode = LAUNCH_STATES
        self.currentState = 0
        self.aborted = False

        # window
        self.setWindowTitle("Mission Control")
        self.setMinimumSize(MIN_SIZE * 2, MIN_SIZE)
        self.setWindowIcon(QIcon(ICON_PATH))
        self.pal = DarkCyanPalette()
        self.setPalette(self.pal)

        self.buttons = {}
        self.dynamicLabels = {}

        # plots
        self.plots = {}
        self.pen = mkPen(color=DETAILING, width=3)
        self.graphData.connect(self.updatePlot)
        setConfigOption("foreground", f"{DETAILING_H}")  # pyqtgraph setting

        # layout
        self.generalLayout = self.createMainGrid()
        centralWidget = QWidget()
        centralWidget.setLayout(self.generalLayout)
        self.setCentralWidget(centralWidget)

        self.serialSet = False
        self.serialOn = False

        self.linkButtons()

        self.locked = False
        self.toggleScreenLock()

        self.decayTestActive = False

        # log start
        start = "NEW SESSION: " + START_TIME
        self.displayPrint(start, reformat=False)
        with open(DATA_LOG_FILE, "a") as datalog:
            datalog.write(start + "\n")

    # SERIAL FUNCTIONS ----------------------------------------------

    def threadingSetup(self, serial: SerialComm) -> None:
        """Sets up threading, serial worker and signals/slots.

        *Serial Window Core
        """
        self.serialThread = QThread()
        self.serialLock = QMutex()
        self.serialWorker = SerialWorker(serial, self.serialLock, "")
        self.serialWorker.moveToThread(self.serialThread)
        self.serialThread.started.connect(self.serialWorker.run)
        self.serialWorker.cleanup.connect(self.serialThread.quit)
        self.serialWorker.error.connect(self.serialError)
        self.serialWorker.msg.connect(self.displayControl)
        self.serialThread.start()

    def selectPort(self) -> bool:
        """Checks for available ports and asks for a selection.

        Returns:
            bool: True setup is successful, False otherwise

        *Serial Window Core
        """
        ports = serial.tools.list_ports.comports()
        if len(ports) < 1:
            self.createConfBox(
                "Serial Error",
                "No COM ports available.\nPlease plug in devices before starting.",
            )
            return False

        warning = (
            'ATTENTION:\nWhen selecting a port, look for "Serial-USB" or your selected platform.'
            + "If you do not see an option like this, please cancel and check your USB connection."
        )
        conf = QMessageBox(
            QMessageBox.Icon.Warning,
            "Setup Confirmation",
            warning,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            self.centralWidget(),
        )

        conf.exec()

        selection, ok = QInputDialog().getItem(
            self.centralWidget(),
            "COM select",
            "Select a port:",
            reversed([f"{desc}" for name, desc, hwid in ports]),
        )
        if not ok:
            return False

        try:
            self.port = str(re.findall(r"COM[0-9]+", selection)[0])  # get port
        except IndexError:
            return False

        return True

    def selectBaud(self) -> bool:
        """Asks for selection of a baudrate.

        Returns:
            bool: True if setup is successful, False otherwise
        """
        selection, ok = QInputDialog().getItem(
            self.centralWidget(),
            "Baudrate select",
            "Select a baudrate:",
            reversed([str(rate) for rate in BAUDRATES]),
        )

        if not ok:
            return False
        try:
            self.baud = int(selection)
        except ValueError:
            error = QMessageBox(
                QMessageBox.Icon.Critical,
                "Setup Error",
                "Setup error detected!",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                self.centralWidget(),
            )
            error.exec()
            return False
        return True

    def setupSerial(self) -> None:
        """Serial option selection."""
        if not self.selectPort() or not self.selectBaud():
            self.serialSet = False
        else:
            self.serialSet = True

    def toggleSerial(self) -> None:
        """Toggles serial connection on/off."""
        if self.serialSet and not self.serialOn:
            try:
                self.serial = setupConnection(self.port, self.baud)
                self.threadingSetup(self.serial)
                self.serialOn = True
                self.buttons[SER_TOGGLE].setText(SER_OFF)
                self.serStartTime = time.time()
            except serial.SerialException:
                self.createConfBox(
                    "Serial Error",
                    "Serial connection could not be established.",
                    QMessageBox.Icon.Critical,
                )
        elif self.serialOn:
            self.serialOn = False
            self.serialWorker.program = False
            time.sleep(0.1)
            if self.serial.connection.is_open:
                self.serial.close()
            self.buttons[SER_TOGGLE].setText(SER_ON)
        else:
            self.createConfBox(
                "Serial Error",
                "Serial settings not configured.",
                QMessageBox.Icon.Critical,
            )

    def closeEvent(self, event) -> None:
        """Adds additional functions when closing window."""
        if self.serialOn:
            self.serialWorker.program = False
            time.sleep(0.1)
            if self.serial.connection.is_open:
                self.serial.close()
        with open(SYS_LOG_FILE, "a") as sysLog, open(DATA_LOG_FILE, "a") as dataLog:
            sysLog.write(
                "---------------------------------------------------------------------------\n"
            )
            dataLog.write(
                "---------------------------------------------------------------------------\n"
            )

    def displayPrint(self, string: str, reformat=True) -> None:
        """Displays to monitor and logs data.

        Args:
            string(str): the string to display and log
            reformat(bool | None): add strFormat if True, otherwise do not
        """
        if reformat:
            string = self.strFormat(string)
        self.monitor.append(string)
        self.monitor.verticalScrollBar().setValue(
            self.monitor.verticalScrollBar().maximum()
        )
        with open(SYS_LOG_FILE, "a") as sysLog:
            sysLog.write(string + "\n")

    def parseData(self, data: str) -> list[tuple]:
        """Parses incoming data to destination/value pairs.

        Args:
            data(str): the incoming data

        Returns:
            list[tuple]: a list of tuples with destination/value pairs

        *Serial Window Core
        """
        if VALVE_TAG in data:
            valve_states = data.strip(VALVE_TAG)
            return [(SV + PIN_READ_MAP[str(i + 1)], valve_states[i]) for i in range(0, 9)]
        if PRESSURE_SEP in data:
            readings = []
            for i, val in enumerate(data.split(PRESSURE_SEP)):
                readings.append((f"{PT}{ANALOG_MAP[i]}", val))
            return readings
        return []

    def updateDisplay(self, dataset: list) -> None:
        """Updates display values, accepting format of parseData.
        Modularize this function if design becomes more complex.

        Args:
            dataset(list): list of parsed data in the format destination, value

        *Serial Window Core
        """
        for dest, value in dataset:
            try:
                if SV in dest:
                    status = int(value)
                    if status:
                        self.dynamicLabels[dest].setStyleSheet(
                            FONT_CSS + f"color: {VALVE_ON}; "
                        )
                        self.dynamicLabels[dest].setText(DISP_FORMAT(dest, "OPEN"))
                    else:
                        self.dynamicLabels[dest].setStyleSheet(
                            FONT_CSS + f"color: {TEXT}; "
                        )
                        self.dynamicLabels[dest].setText(DISP_FORMAT(dest, "CLOSE"))
                elif PT in dest:
                    try:
                        reading = int(value.strip())
                    except ValueError:
                        return

                    # numerical readings
                    self.dynamicLabels[dest].setText(DISP_FORMAT(dest, reading))
                    if reading in SAFE_PRESS:
                        self.dynamicLabels[dest].setStyleSheet(PRESS_GREEN)
                    elif reading in MID_PRESS:
                        self.dynamicLabels[dest].setStyleSheet(PRESS_YELLOW)
                    else:
                        self.dynamicLabels[dest].setStyleSheet(PRESS_RED)

                    # graphs
                    if dest == PT + "2":  # Ox line
                        self.graphData.emit(FUEL_GRAPH, reading) 
                    elif dest == PT + "3":  # Fuel line
                        self.graphData.emit(OX_GRAPH, reading)
            except KeyError:
                continue

    @pyqtSlot(str)
    def displayControl(self, string: str) -> None:
        """Prints to display monitor, parses data, and updates live labels.

        Args:
            string(str): the incoming data

        *Serial Window Core
        """
        with open(DATA_LOG_FILE, "a") as sysLog:
            sysLog.write(self.strFormat(string) + "\n")
        data = self.parseData(string.strip('\n'))
        self.updateDisplay(data)

    def sendMessage(self, command: (str | None) = None) -> None:
        """Sends a specific message to toggle.

        *Serial Window Core
        """
        if self.serialSet and self.serialOn:
            if not command:
                command = self.serialEntry.text()
            if len(set(command)) < len(command):
                self.createConfBox(
                    "Serial Message Warning",
                    "Duplicate pin detected - please try again.",
                )
                return
            self.displayPrint(f"Send: {MSG_PAD(command)}")
            self.serialWorker.sendToggle(MSG_PAD(command))
        else:
            self.createConfBox(
                "Serial Error",
                "Serial must be configured and on.",
                QMessageBox.Icon.Critical,
            )

    def serialError(self) -> None:
        """Displays error popup upon handling of a serial exception."""
        self.createConfBox(
            "Serial Error",
            "Serial error detected! Please try again.",
            QMessageBox.Icon.Warning,
        )
        self.toggleSerial()

    def strFormat(self, string: str) -> str:
        """Returns formatted string for monitor display.

        Args:
            string(str): the string to format

        Returns:
            str: the formatted string
        """
        return QDateTime.currentDateTime().toString(DATE_TIME_FORMAT) + string.strip()

    # WINDOW ELEMENTS -----------------------------------------------

    def createLabelBox(
        self,
        message: str | None = None,
        labelType: str | None = None,
        style: str | None = None,
    ) -> QLabel:
        """Creates frame box with optional label message.

        Args:
            labelType(str): labelType to map label to dict of labels.
            message(str): the label message itself.
            style(str): style sheet configurations.

        Returns:
            QLabel: the generated frame box with optional message.
        """
        label = QLabel()
        if labelType:
            pos = QGridLayout(label)
            self.dynamicLabels[labelType] = QLabel(message)
            if style:
                self.dynamicLabels[labelType].setStyleSheet(style)
            pos.addWidget(self.dynamicLabels[labelType])
        label.setFrameStyle(QFrame.Shape.Panel)
        label.setPalette(self.pal)
        label.setLineWidth(1)
        return label

    def createLayout(self, parent, widgets: list[tuple]) -> QGridLayout:
        """Creates a layout of widgets.

        Args:
            widgets(list): list of widgets to place in layout,
            plus layout

        Returns:
            QGridLayout: layout of widgets

        Widget list format example:
        [(button, x, y, h, l), (button, x, y, h, l), (someWidget, x, y, h, l)]
        """
        pos = QGridLayout(parent)
        for widget in widgets:
            params = len(widget)
            if params == 3:  # widget, x, y
                pos.addWidget(widget[0], widget[1], widget[2])
            elif params == 5:  # widget, x, y, height, length
                pos.addWidget(widget[0], widget[1], widget[2], widget[3], widget[4])
        return pos

    def createLayoutBox(self, widgets: list[tuple]) -> QLabel:
        """Creates a frame box with layout of widgets.

        Args:
            widgets(list): list of widgets to place in layout,
            plus grid location.

        Returns:
            QLabel: the generated frame box with given widgets.

        Widget list format example:
        [(button, x, y, h, l), (button, x, y, h, l), (someWidget, x, y, h, l)]
        """
        label = QLabel()
        self.createLayout(label, widgets)
        label.setFrameStyle(QFrame.Shape.Panel)
        label.setLineWidth(1)
        return label

    def createConfBox(
        self,
        title: str,
        message: str,
        icon: QMessageBox.Icon = QMessageBox.Icon.Warning,
        default: bool = True,
    ) -> bool:
        """Creates a confirmation box.

        Args:
            title(str): title of the box window
            message(str): the message to display
            icon(QMessageBox.Icon): the icon for the window
            default(bool): default button ok (True) or cancel (False)
        """
        conf = QMessageBox(
            icon,
            title,
            message,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            self.centralWidget(),
        )
        if not default:
            conf.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if conf.exec() == QMessageBox.StandardButton.Ok:
            return True
        return False

    def createMainGrid(self) -> QGridLayout:
        """Creates primary display grid with frame boxes and components.

        Returns:
            QGridLayout: the primary frame layout
        """
        grid = QGridLayout()
        grid.setHorizontalSpacing(1)
        grid.setVerticalSpacing(1)

        # top row
        self.clock = Clock(f"color: {TEXT}; {FONT_CSS} {FONT_SIZE(16)}")

        grid.addWidget(
            self.createLabelBox(
                "<h1> VEHICLE STATUS </h1>", STATUS_LABEL, HEADER_STYLE
            ),
            0,
            0,
            1,
            3,
        )
        grid.addWidget(
            self.createLabelBox(
                "<h1> FLUIDS CONTROL DISPLAY </h1>", DIAGRAM_LABEL, HEADER_STYLE
            ),
            0,
            3,
            1,
            6,
        )
        grid.addWidget(
            self.createLayoutBox([(self.clock.dateTime, 0, 0, 1, 1)]), 0, 9, 1, 3
        )

        # left column
        self.createProcedure()

        grid.addWidget(
            self.createLabelBox(
                f"<h1>STAGE: {LAUNCH_STATES[self.currentState]}</h1>",
                CURR_STATE,
                HEADER_STYLE,
            ),
            1,
            0,
            1,
            3,
        )
        grid.addWidget(self.createLayoutBox(self.createProcedure()), 2, 0, 8, 3)
        grid.addWidget(
            self.createLayoutBox(
                self.createButtonSets([(PREVIOUS, 0, 0, 1, 1), (PROCEED, 0, 1, 1, 1)])
            ),
            10,
            0,
            2,
            3,
        )
        grid.addWidget(
            self.createLabelBox("<h1>ABORT MISSION: </h1>", ABORT, HEADER_STYLE),
            12,
            0,
            1,
            3,
        )
        grid.addWidget(
            self.createLayoutBox(
                self.createButtonSets(
                    [(ABORT_MISSION, 0, 0, 1, 1)]
                    #[(OVERPRESSURE, 0, 0, 1, 1), (IGNITION_FAILURE, 0, 1, 1, 1)]
                )
            ),
            13,
            0,
            1,
            3,
        )

        # middle column
        grid.addWidget(self.createWireDiagram(), 1, 3, 13, 6)

        # right column
        grid.addWidget(self.createLayoutBox(self.createGraphWidgets()), 1, 9, 10, 3)
        grid.addWidget(
            self.createLayoutBox(
                self.createButtonSets([(SETUP_SER, 0, 0, 1, 1), (SER_ON, 0, 1, 1, 1)])
            ),
            11,
            9,
            1,
            3,
        )
        grid.addWidget(self.createLayoutBox(self.createSerialLayout()), 12, 9, 2, 3)

        return grid

    def createProcedure(self) -> list[tuple]:
        """Creates procedure."""
        labels = []
        for i, stage in enumerate(LAUNCH_STATES):
            self.dynamicLabels[stage] = QLabel(f"{i + 1}. {stage}")
            self.dynamicLabels[stage].setStyleSheet(STAGE_FONT_WHITE)
            labels.append((self.dynamicLabels[stage], i, 0, 1, 1))
        self.dynamicLabels[LAUNCH_STATES[0]].setStyleSheet(STAGE_FONT_BLUE)
        return labels

    def createSerialLayout(self) -> list:
        """Creates and returns items of the serial setup for a layoutBox."""
        # Serial monitor box
        self.monitor = QTextEdit()
        self.monitor.setReadOnly(True)
        self.monitor.setFrameStyle(QFrame.Shape.NoFrame)
        self.monitor.setStyleSheet(COLOR_CSS)

        # Message entry line
        self.serialEntry = QLineEdit()
        self.serialEntry.setStyleSheet(COLOR_CSS + FONT_CSS)
        self.serialEntry.setMaximumHeight(LINE_HEIGHT)

        # Send button
        self.buttons[SERIAL_SEND] = QPushButton(SERIAL_SEND)
        self.buttons[SERIAL_SEND].setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons[SERIAL_SEND].setStyleSheet(BUTTON_STYLE)

        return [
            (self.serialEntry, 0, 0, 1, 1),
            (self.buttons[SERIAL_SEND], 0, 1, 1, 1),
            (self.monitor, 1, 0, 1, 2),
        ]
    
    def sendIgnitionCmd(self) -> None:
        """Sends ignition command when ignite button is pressed."""
        if self.currentState == len(LAUNCH_STATES) - 1:
            self.displayPrint("Ignition command sent.")
            self.sendMessage(IGNITE_CMD)
            self.dynamicLabels[IGNITE].setStyleSheet(PRESS_YELLOW) # move to updateDisplay via state updates

    def sendMainValvesCmd(self) -> None:
        """Sends command to open main valves for fire when MV button is pressed."""
        if self.currentState == len(LAUNCH_STATES) - 1:
            self.displayPrint("Main valve actuation executed.")
            self.sendMessage(MAINVALVE_CMD)
            self.dynamicLabels[MAINVALVES].setStyleSheet(PRESS_YELLOW) # move to updateDisplay via state updates

    def createWireDiagram(self) -> QLabel:
        """Creates wire diagram."""
        frame = QLabel()
        frame.setFrameStyle(QFrame.Shape.Panel)
        frame.setLineWidth(1)
        labelLayout = QGridLayout(frame)

        # image
        imageLabel = QLabel()
        imageLabel.setFixedSize(420, 560)
        image = QSvgWidget(WIRE_DIAGRAM, imageLabel)
        image.setGeometry(0, 0, 420, 560)

        # data
        for i in range(1, 10):
            name = SV + str(i)
            self.dynamicLabels[name] = QLabel(DISP_FORMAT(name, "CLOSE"))
            self.dynamicLabels[name].setStyleSheet(SV_CSS)
            self.dynamicLabels[name].setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.buttons[name] = QPushButton(f"{SV_NAMES[name]}")
            self.buttons[name].setStyleSheet(BUTTON_STYLE)

        for i in range(1, 5):
            name = PT + str(i)
            self.dynamicLabels[name] = QLabel(DISP_FORMAT(name, "N/A"))
            self.dynamicLabels[name].setStyleSheet(PRESS_GREEN)
            self.dynamicLabels[name].setAlignment(Qt.AlignmentFlag.AlignCenter)

        # boxes
        t1 = QLabel("N2/COPV")
        t1.setStyleSheet(f"{FONT_CSS} color: {DETAILING_H}; {BOLD}")
        t1.setAlignment(Qt.AlignmentFlag.AlignCenter)

        box1 = self.createLayoutBox(
            [
                (t1, 0, 0, 1, 1),
                (self.dynamicLabels[SV + "1"], 1, 0, 1, 1),
                (self.buttons[SV + "1"], 2, 0, 1, 1),
                (self.dynamicLabels[SV + "2"], 3, 0, 1, 1),
                (self.buttons[SV + "2"], 4, 0, 1, 1),
            ]
        )

        t2 = QLabel("High Press")
        t2.setStyleSheet(f"{FONT_CSS} color: {DETAILING_H}; font-size: 11px; {BOLD}")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box2 = self.createLayoutBox(
            [
                (t2, 0, 0, 1, 1),
                (self.dynamicLabels[PT + "1"], 1, 0, 1, 1),
                (self.dynamicLabels[SV + "3"], 2, 0, 1, 1),
                (self.buttons[SV + "3"], 3, 0, 1, 1),
            ]
        )

        t3 = QLabel("Ox/N2O")
        t3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t3.setStyleSheet(f"{FONT_CSS} font-size: 11px; color: {DETAILING_H}; {BOLD}")
        box3 = self.createLayoutBox(
            [
                (t3, 0, 0, 1, 1),
                (self.dynamicLabels[PT + "2"], 1, 0, 1, 1),
                (self.dynamicLabels[SV + "5"], 2, 0, 1, 1),
                (self.buttons[SV + "5"], 3, 0, 1, 1),
                (self.dynamicLabels[SV + "6"], 4, 0, 1, 1),
                (self.buttons[SV + "6"], 5, 0, 1, 1),
            ]
        )

        t4 = QLabel("Fuel/Kero")
        t4.setStyleSheet(f"{FONT_CSS} color: {DETAILING_H}; {BOLD};")
        t4.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box4 = self.createLayoutBox(
            [
                (t4, 0, 0, 1, 1),
                (self.dynamicLabels[PT + "3"], 1, 0, 1, 1),
                (self.dynamicLabels[SV + "4"], 2, 0, 1, 1),
                (self.buttons[SV + "4"], 3, 0, 1, 1),
                (self.dynamicLabels[SV + "7"], 4, 0, 1, 1),
                (self.buttons[SV + "7"], 5, 0, 1, 1),
            ]
        )

        t5 = QLabel("Main Valve")
        t5.setStyleSheet(f"{FONT_CSS} color: {DETAILING_H}; {BOLD}")
        t5.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box5 = self.createLayoutBox(
            [
                (t5, 0, 0, 1, 2),
                (self.dynamicLabels[SV + "8"], 1, 0, 1, 1),
                (self.buttons[SV + "8"], 1, 1, 1, 1),
                (self.dynamicLabels[SV + "9"], 2, 0, 1, 1),
                (self.buttons[SV + "9"], 2, 1, 1, 1),
                (self.dynamicLabels[PT + "4"], 4, 0, 1, 2),
            ]
        )

        self.buttons[DT] = QPushButton(DT)
        self.buttons[DT].setStyleSheet(BUTTON_STYLE)

        #desperate times call for desperate measures
        for name in (IGNITE, MAINVALVES):
            self.dynamicLabels[name] = QLabel(name)
            self.dynamicLabels[name].setStyleSheet(SV_CSS)
            self.dynamicLabels[name].setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.buttons[name] = QPushButton(f"{name}")
            self.buttons[name].setStyleSheet(BUTTON_STYLE)

        t6 = QLabel("Fire")
        t6.setStyleSheet(f"{FONT_CSS} color: {DETAILING_H}; {BOLD}")
        t6.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box6 = self.createLayoutBox(
            [
                (t6, 0, 0, 1, 2),
                (self.dynamicLabels[IGNITE], 1, 0, 1, 1),
                (self.buttons[IGNITE], 2, 0, 1, 1),
                (self.dynamicLabels[MAINVALVES], 3, 0, 1, 1),
                (self.buttons[MAINVALVES], 4, 0, 1, 1),
            ]
        )

        self.buttons[LOCK] = QPushButton(LOCK)
        self.buttons[LOCK].setStyleSheet(BUTTON_STYLE)

        # layout
        labelLayout.addWidget(imageLabel, 0, 4, 13, 12)
        labelLayout.addWidget(box1, 1, 0, 3, 2)
        labelLayout.addWidget(box2, 0, 11, 3, 3)
        labelLayout.addWidget(box3, 5, 0, 4, 2)
        labelLayout.addWidget(box4, 5, 14, 4, 2)
        labelLayout.addWidget(box5, 10, 11, 4, 4)
        labelLayout.addWidget(box6, 10, 0, 4, 2)
        labelLayout.addWidget(self.buttons[DT], 14, 11, 1, 4)
        labelLayout.addWidget(self.buttons[LOCK], 14, 0, 1, 2)

        return frame

    def createPlot(self) -> dict:
        """Creates a graph.

        Returns:
            tuple: the widget, graph, time set, data set
        """
        widget = PlotWidget()

        # sample size 600 is abt a minute before scrolling
        time = [0] * PSI_SAMPLE_SIZE  # time points
        data = [0] * PSI_SAMPLE_SIZE  # data points

        widget.setBackground(f"{PRIMARY_H}")
        widget.setYRange(-50, 550)
        widget.setMouseEnabled(x=False, y=False)
        widget.hideButtons()
        graph = widget.plot(time, data, pen=self.pen)
        psiChange = QLabel(f"{PSI_CHANGE}: N/A")
        psiChange.setStyleSheet(f"{GREEN}{BOLD}")

        graphItems = {
            WIDGET: widget,
            GRAPH: graph,
            TIME: time,
            DATA: data,
            TIMESTAMP: 0,
            PSI_CHANGE: psiChange,
        }

        return graphItems

    def createGraphWidgets(self) -> list[tuple]:
        """Creates graph widgets for layoutBox.

        Returns:
            list[tuple]: list of plots in (widget, x, y, l, h)
        """
        fuelLabel = QLabel(FUEL_GRAPH)
        fuelLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fuelLabel.setStyleSheet(STAGE_FONT_BLUE)
        self.plots[FUEL_GRAPH] = self.createPlot()

        oxLabel = QLabel(OX_GRAPH)
        oxLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        oxLabel.setStyleSheet(STAGE_FONT_BLUE)
        self.plots[OX_GRAPH] = self.createPlot()

        return [
            (fuelLabel, 0, 0, 5, 5),
            (self.plots[FUEL_GRAPH][WIDGET], 5, 0, 5, 5),
            (self.plots[FUEL_GRAPH][PSI_CHANGE], 5, 3, 1, 2),
            (oxLabel, 10, 0, 5, 5),
            (self.plots[OX_GRAPH][WIDGET], 15, 0, 5, 5),
            (self.plots[OX_GRAPH][PSI_CHANGE], 15, 3, 1, 2),
        ]

    @pyqtSlot(str, int)
    def updatePlot(self, plotName: str, data: int) -> None:
        """Updates a plot."""

        plot = self.plots[plotName]

        # time
        plot[TIMESTAMP] = time.time() - self.serStartTime
        plot[TIME] = plot[TIME][1:]
        plot[TIME].append(plot[TIMESTAMP])

        # data
        try:
            plot[DATA] = plot[DATA][1:]
            plot[DATA].append(data)
        except ValueError:
            pass

        # Update the data.
        #psiChangePerMin = (plot[DATA][-1] - plot[DATA][0])
        psiChangePerMin = (np.mean(plot[DATA][:ROLLING_AVG_SAMPLE_SIZE] - np.mean(plot[DATA][-ROLLING_AVG_SAMPLE_SIZE:])))
        plot[GRAPH].setData(plot[TIME][-DISPLAYED_SAMPLE_SIZE:-1], plot[DATA][-DISPLAYED_SAMPLE_SIZE:-1])
        plot[PSI_CHANGE].setText(PSI_PER_MIN(psiChangePerMin))

    def createButtonSets(self, keys: list[tuple]) -> list[tuple]:
        """Generates a set of buttons compatible with layoutBox

        Args:
            keys(list[str]): list of button names (for dictionary hashing)

        Returns:
            list[tuple]: list of tuples with buttons and x, y grid locations.
        """
        buttonDisplay = []
        for key, w, x, y, z in keys:
            self.buttons[key] = QPushButton(key)
            self.buttons[key].setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.buttons[key].setStyleSheet(BUTTON_STYLE)
            buttonDisplay.append((self.buttons[key], w, x, y, z))
        return buttonDisplay

    def updateStage(self) -> None:
        """Confirms to update the stage."""
        if not self.aborted:
            if not self.createConfBox(
                "Stage Advancement", "Confirm: advance to next stage?", default=False
            ):
                return
            if self.currentState + 1 >= len(LAUNCH_STATES):
                self.createConfBox("Stage Advancement", "No more stages remaining.")
                return

            # Change highlight
            self.dynamicLabels[LAUNCH_STATES[self.currentState]].setStyleSheet(
                STAGE_FONT_WHITE
            )
            self.currentState += 1
            self.dynamicLabels[LAUNCH_STATES[self.currentState]].setStyleSheet(
                STAGE_FONT_BLUE
            )

            # Change title
            self.dynamicLabels[CURR_STATE].setText(
                f"<h1>STAGE: {LAUNCH_STATES[self.currentState]}</h1>"
            )

            self.displayPrint(f"Advance to: {LAUNCH_STATES[self.currentState]}")

    def previousStage(self) -> None:
        """Confirms to return to last stage."""
        if self.aborted:
            if not self.createConfBox(
                "Stage Regression", "Confirm: exit abort state?", default=False
            ):
                return
            self.aborted = False
        else:
            if not self.createConfBox(
                "Stage Regression", "Confirm: return to last stage?", default=False
            ):
                return
        if self.currentState - 1 < 0:
            self.createConfBox(
                "Stage Regression", "Cannot return further than first stage."
            )
            return

        # Change highlight
        self.dynamicLabels[LAUNCH_STATES[self.currentState]].setStyleSheet(
            STAGE_FONT_WHITE
        )
        self.currentState -= 1
        self.dynamicLabels[LAUNCH_STATES[self.currentState]].setStyleSheet(
            STAGE_FONT_BLUE
        )

        # Change title
        self.dynamicLabels[CURR_STATE].setText(
            f"<h1>STAGE: {LAUNCH_STATES[self.currentState]}</h1>"
        )

        # Reset Ignition and MV
        self.dynamicLabels[IGNITE].setStyleSheet(FONT_CSS + f"color: {TEXT}; ")
        self.dynamicLabels[MAINVALVES].setStyleSheet(FONT_CSS + f"color: {TEXT}; ")

        self.displayPrint(f"Return to: {LAUNCH_STATES[self.currentState]}")

    def abortMission(self, confirmation: str) -> bool:
        """Abort mission confirmation.

        Args:
            confirmation(str): the confirmation message to ask

        Returns:
            bool: abortion confirmation status
        """
        if LAUNCH_STATES[self.currentState] != 'IDLE' and self.createConfBox(
            "Mission Abort Confirmation", confirmation, default=False
        ):
            self.dynamicLabels[CURR_STATE].setText("<h1> MISSION ABORTED </h1>")
            self.aborted = True
            try:
                self.countdown.stop()
            except AttributeError:
                pass
            return True
        return False
    
    def abortGeneral(self) -> None:
        """Begins abort sequence on confirmation."""
        if not self.aborted:
            self.displayPrint("System abort executed.")
            self.sendMessage(ABORT_CMD)

    # def abortOverpressure(self) -> None:
    #     """Begins overpressurization abort sequence on confirmation."""
    #     if not self.aborted:
    #         if self.abortMission("Begin overpressurization abort sequence?"):
    #             self.displayPrint("System aborted for overpressurization.")
    #             print("Change task display: beginning pressure relief sequence.")
    #             print("Close K-bottle SV.")
    #             print("Open Bottom right SV")
    #             print("Open Bottom Left SV")
    #             print("Open Fuel line SV")
    #             print("Open Ox line SV")
    #             print("Open top center SV")
    #             print("Change task display: Overpressure abort sequence complete.")

    # def abortIgnitionFail(self) -> None:
    #     """Begins ignition fail abort sequence on confirmation."""
    #     if not self.aborted:
    #         if self.abortMission("Begin ignition fail abort sequence?"):
    #             self.displayPrint("System aborted for ignition failure.")
    #             print("Change task display: Ignition failure: entering HOLD stage.")
    #             print("Closing K-bottle SV")
    #             print("Close Bottom right SV")
    #             print("Close Bottom Left SV")
    #             print("Close Fuel line SV")
    #             print("Close Ox line SV")
    #             print("Close top center SV")
    #             print("Change task display: HOLD STAGE")

    def toggleScreenLock(self) -> None:
        """Toggles acces to buttons."""
        self.locked = not self.locked
        for key in self.buttons.keys():
            if key != LOCK:
                self.buttons[key].setEnabled(not self.locked)

        if self.locked:
            self.buttons[LOCK].setText("Unlock")
        else:
            self.buttons[LOCK].setText("Lock")

    def linkButtons(self) -> None:
        """Link buttons to functionality."""
        self.buttons[PROCEED].clicked.connect(self.updateStage)
        self.buttons[PREVIOUS].clicked.connect(self.previousStage)
        # self.buttons[OVERPRESSURE].clicked.connect(self.abortOverpressure)
        # self.buttons[IGNITION_FAILURE].clicked.connect(self.abortIgnitionFail)
        self.buttons[ABORT_MISSION].clicked.connect(self.abortGeneral)
        self.buttons[SER_TOGGLE].clicked.connect(self.toggleSerial)
        self.buttons[SETUP_SER].clicked.connect(self.setupSerial)
        self.buttons[SERIAL_SEND].clicked.connect(self.sendMessage)
        self.buttons[LOCK].clicked.connect(self.toggleScreenLock)
        self.buttons[IGNITE].clicked.connect(self.sendIgnitionCmd)
        self.buttons[MAINVALVES].clicked.connect(self.sendMainValvesCmd)
        self.buttons[DT].clicked.connect(self.decayTest)

        # create individual SV button initializer functions
        # old method to list comprehend functions for linking buttons to send respective numbers in range
        #svButtons = [
        #    lambda num=str(i): self.buttons[SV + num].clicked.connect(
        #        lambda: self.sendMessage(num)
        #    )
        #    for i in range(1, 10)
        #]

        # call initializer functions to create buttons

        # The two lambdas are needed because you need a list of functions that 
        # have a different function as a parameter, without a lambda for the parameter
        # you'd end up having the same lambda for every button
        svButtons = [
            lambda label=str(i), num=str(PIN_MAP[i - 1]): self.buttons[SV + label].clicked.connect(
                lambda: self.sendMessage(num)
            )
            for i in range(1, 10) # range 1 to 9
        ]

        for func in svButtons:
            func()

    def countDown(self) -> None:
        """Starts countdown"""
        if not self.aborted:
            self.moment = 11

            def countSecond():
                self.moment -= 1
                if self.moment == 0:
                    self.moment = "BLASTOFF"
                    self.countdown.stop()
                self.dynamicLabels[CURR_STATE].setText(
                    f"<h1> COUNTDOWN: {self.moment} </h1>"
                )

            countSecond()
            self.countdown = QTimer()
            self.countdown.timeout.connect(countSecond)
            self.countdown.start(1000)
    
    def decayTest(self) -> None:
        """Performs decay test."""

        if not self.serialSet or not self.serialOn:
            self.createConfBox(
                "Serial Error",
                "Serial settings not configured.",
                QMessageBox.Icon.Critical,
            )
            return
        
        if (self.decayTestActive):
            if self.createConfBox(
                "Decay Test",
                "Stop Decay Test?", default=False
            ):
                self.decayTimer.stop()
                self.displayPrint("Decay Test terminated early.")
                self.decayTestActive = False
                self.buttons[DT].setText(DT)
                return
        else:
            self.decayTestActive = True
            self.buttons[DT].setText(DT_STOP)

        self.iterations = DT_ITERS
        self.it_time = DT_ITER_LEN_SECONDS
        self.dtReadings = {}
        self.dtAvg = {}
        for i in ACTIVE_PTS:
            self.dtReadings[i] = []
            self.dtAvg[i] = 0

        self.displayPrint(
            f"Decay Test: {self.iterations} interations, {self.it_time} seconds per iter."
        )

        def benchmark():
            if self.iterations == 0:
                self.decayTimer.stop()
                avgStr = f"Averages (PSI): "
                for i in ACTIVE_PTS:
                    total = np.diff(np.array(self.dtReadings[i]))

                    avgStr += f"{i}-{np.average(total)} "
                self.displayPrint(avgStr)
                self.displayPrint("Decay Test Complete.")
                self.decayTestActive = False
                self.buttons[DT].setText(DT)
                return
            update = f"DT{self.iterations}: "
            for i in ACTIVE_PTS:
                r = self.dynamicLabels[i].text().split(":")[1]
                self.dtReadings[i].append(int(r))
                update += f"{i}-{r} "
            self.displayPrint(update)
            self.iterations -= 1

        benchmark()
        self.decayTimer = QTimer()
        self.decayTimer.timeout.connect(benchmark)
        self.decayTimer.start(self.it_time * 1000)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    rocketDisplay = RocketDisplayWindow()
    rocketDisplay.showMaximized()
    sys.exit(app.exec())
