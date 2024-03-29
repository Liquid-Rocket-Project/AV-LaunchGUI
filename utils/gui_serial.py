"""
Author: Nick Fan
Date: 3/2023
Description: Serial module for use with PyQt6 applications.
"""

import time
import serial
import serial.tools.list_ports
from PyQt6.QtCore import QMutex, QObject, pyqtSignal

BAUDRATES = [9600, 115200]

# CLASSES ------------------------------------------------------------------------|
class SerialComm:
    """Serial Com Manager."""

    def __init__(self, com: str, baudrate: int) -> None:
        """Creates new serial com manager.

        Args:
            com(str): the COM port
            baudrate(int): the baudrate
        """
        self.port = com
        self.baudrate = baudrate
        self.connection = serial.Serial(
            self.port, self.baudrate, timeout=0.05, write_timeout=0.1, xonxoff=True
        )

    def receiveMessage(self) -> str:
        """Read from serial com if there is data in."""
        if not self.connection.is_open:
            self.connection.open()
        try:
            data = str(self.connection.readall().decode("ascii"))
            if data:
                return data
        except serial.SerialException:
            pass
        return ""

    def readEolLine(self) -> bytearray:
        """Reads line specifically using LF for eol.

        EoL readline by: lou under CC BY-SA 3.0
        src: https://stackoverflow.com/questions/16470903/pyserial-2-6-specify-end-of-line-in-readline
        Changes have been made to adjust for integration in this program.
        """
        eol = b"\n"
        eolLen = len(eol)
        line = bytearray()
        while True:
            c = self.connection.read(1)
            if c:
                line += c
                if line[-eolLen:] == eol:
                    break
            else:
                break
        return line

    def sendMessage(self, message: str) -> bool:
        """Writes to serial com."""
        if not self.connection.is_open:
            self.connection.open()
        try:
            self.connection.write(message.encode("utf-8"))
            return True
        except (serial.SerialException, serial.SerialTimeoutException):
            return False

    def close(self):
        """Closes the com connection."""
        self.connection.close()


class SerialWorker(QObject):
    """GUI Serial Manager Thread."""

    msg = pyqtSignal(str)
    cleanup = pyqtSignal()
    error = pyqtSignal()

    def __init__(self, connection: SerialComm, lock: QMutex, pins: str, parent=None) -> None:
        """Constructs new Serial Worker.

        Args:
            connection(SerialComm): the serial connection to use
            pins(str): pins to toggle
            parent(QObject): optional parent
        """
        super().__init__(parent)
        self.serialConnection = connection
        self.pins = pins
        self.mutex = lock
        self.program = True

    def setPins(self, newPins: str) -> None:
        """Sets new pins.

        Args:
            newPins(str): a new set of pins to toggle.
        """
        self.pins = newPins

    def run(self) -> None:
        """Sends initial toggle and continuously reads
        until indicated to stop, then toggles again."""
        # read serial
        error = False
        while self.program:
            if not error:
                if self.mutex.tryLock():

                    try:
                        received = []
                        received.append(self.serialConnection.connection.readline().decode())
                        while self.serialConnection.connection.in_waiting > 8:
                            received.append(self.serialConnection.connection.readline().decode())
                    except (serial.SerialException, UnicodeDecodeError):
                        self.error.emit()
                        error = True
                        received = None

                    time.sleep(0.05)
                    self.mutex.unlock()
                    if received:
                        if len(received) == 0:
                            continue
                        for x in received:
                            self.msg.emit(x)
    
        self.cleanup.emit()

    def sendToggle(self, pins: str | None = None) -> None:
        """Sends message, which by default is the pins instance variable.
        
        Args:
            pins(str): optional argument to indicate pins to toggle.
        """
        if pins:
            message = pins + "\n"
        else:
            message = self.pins + "\n"
        while True:
            if self.mutex.tryLock():
                self.serialConnection.sendMessage(message)
                self.mutex.unlock()
                break

# FUNCTIONS ----------------------------------------------------------------------|
def setupConnection(selectedPort: str, baud: int) -> SerialComm:
        """Sets up and returns a serial comm.

        Args:
            seletedPort(str): the selected COM port
            baud(int): the desired baudrate
        Returns:
            SerialComm: a serial connection object
        
        *Serial Window Core
        """
        ser = SerialComm(selectedPort, baud)
        return ser
