from PySide2 import QtCore
from PySide2.QtCore import Qt, QAbstractItemModel, QModelIndex, QSize, QTimer
from PySide2.QtGui import QPalette, QFontMetricsF
from PySide2.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget, QStyle, QSplitter, QLabel

import threading

import binaryninjaui
from binaryninja import BinaryView, PythonScriptingInstance, InstructionTextToken, InstructionTextTokenType, DisassemblyTextLine, LinearDisassemblyLine, LinearDisassemblyLineType, HighlightStandardColor
from binaryninja.enums import InstructionTextTokenType
from binaryninjaui import View, ViewType, UIAction, UIActionHandler, LinearView, DisassemblyContainer, ViewFrame, DockHandler, TokenizedTextView, HistoryEntry

from . import widget, ControlsWidget
from .. import binjaplug

class DebugView(QWidget, View):
	class DebugViewHistoryEntry(HistoryEntry):
		def __init__(self, memory_addr, address, is_raw):
			HistoryEntry.__init__(self)

			self.memory_addr = memory_addr
			self.address = address
			self.is_raw = is_raw

		def __repr__(self):
			if self.is_raw:
				return "<raw history: {}+{:0x} (memory: {:0x})>".format(self.address[0], self.address[1], self.memory_addr)
			return "<code history: {:0x} (memory: {:0x})>".format(self.address, self.memory_addr)

	def __init__(self, parent, data):
		if not type(data) == BinaryView:
			raise Exception('expected widget data to be a BinaryView')

		self.bv = data

		self.debug_state = binjaplug.get_state(data)
		memory_view = self.debug_state.memory_view
		self.debug_state.ui.debug_view = self

		QWidget.__init__(self, parent)
		self.controls = ControlsWidget.DebugControlsWidget(self, "Controls", data, self.debug_state)
		View.__init__(self)

		self.setupView(self)

		self.current_offset = 0

		self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

		frame = ViewFrame.viewFrameForWidget(self)
		self.memory_editor = LinearView(memory_view, frame)
		self.binary_editor = DisassemblyContainer(frame, data, frame)

		self.binary_text = TokenizedTextView(self, memory_view)
		self.is_raw_disassembly = False
		self.raw_address = 0

		self.is_navigating_history = False
		self.memory_history_addr = 0

		# TODO: Handle these and change views accordingly
		# Currently they are just disabled as the DisassemblyContainer gets confused
		# about where to go and just shows a bad view
		self.binary_editor.getDisassembly().actionHandler().bindAction("View in Hex Editor", UIAction())
		self.binary_editor.getDisassembly().actionHandler().bindAction("View in Linear Disassembly", UIAction())
		self.binary_editor.getDisassembly().actionHandler().bindAction("View in Types View", UIAction())

		self.memory_editor.actionHandler().bindAction("View in Hex Editor", UIAction())
		self.memory_editor.actionHandler().bindAction("View in Disassembly Graph", UIAction())
		self.memory_editor.actionHandler().bindAction("View in Types View", UIAction())

		small_font = QApplication.font()
		small_font.setPointSize(11)

		bv_layout = QVBoxLayout()
		bv_layout.setSpacing(0)
		bv_layout.setContentsMargins(0, 0, 0, 0)

		bv_label = QLabel("Loaded File")
		bv_label.setFont(small_font)
		bv_layout.addWidget(bv_label)
		bv_layout.addWidget(self.binary_editor)

		self.bv_widget = QWidget()
		self.bv_widget.setLayout(bv_layout)

		disasm_layout = QVBoxLayout()
		disasm_layout.setSpacing(0)
		disasm_layout.setContentsMargins(0, 0, 0, 0)

		disasm_label = QLabel("Raw Disassembly at PC")
		disasm_label.setFont(small_font)
		disasm_layout.addWidget(disasm_label)
		disasm_layout.addWidget(self.binary_text)

		self.disasm_widget = QWidget()
		self.disasm_widget.setLayout(disasm_layout)

		memory_layout = QVBoxLayout()
		memory_layout.setSpacing(0)
		memory_layout.setContentsMargins(0, 0, 0, 0)

		memory_label = QLabel("Debugged Process")
		memory_label.setFont(small_font)
		memory_layout.addWidget(memory_label)
		memory_layout.addWidget(self.memory_editor)

		self.memory_widget = QWidget()
		self.memory_widget.setLayout(memory_layout)

		self.splitter.addWidget(self.bv_widget)
		self.splitter.addWidget(self.memory_widget)

		# Equally sized
		self.splitter.setSizes([0x7fffffff, 0x7fffffff])

		layout = QVBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)
		layout.addWidget(self.controls)
		layout.addWidget(self.splitter, 100)
		self.setLayout(layout)

		self.needs_update = True
		self.update_timer = QTimer(self)
		self.update_timer.setInterval(200)
		self.update_timer.setSingleShot(False)
		self.update_timer.timeout.connect(lambda: self.updateTimerEvent())

		self.add_scripting_ref()

	def add_scripting_ref(self):
		# Hack: The interpreter is just a thread, so look through all threads
		# and assign our state to the interpreter's locals
		for thread in threading.enumerate():
			if type(thread) == PythonScriptingInstance.InterpreterThread:
				thread.locals["dbg"] = self.debug_state

	def getData(self):
		return self.bv

	def getFont(self):
		return binaryninjaui.getMonospaceFont(self)

	def getCurrentOffset(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentOffset()
		return self.raw_address

	def getSelectionOffsets(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getSelectionOffsets()
		return (self.raw_address, self.raw_address)

	def getCurrentFunction(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentFunction()
		return None

	def getCurrentBasicBlock(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentBasicBlock()
		return None

	def getCurrentArchitecture(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentArchitecture()
		return None

	def getCurrentLowLevelILFunction(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentLowLevelILFunction()
		return None

	def getCurrentMediumLevelILFunction(self):
		if not self.is_raw_disassembly:
			return self.binary_editor.getDisassembly().getCurrentMediumLevelILFunction()
		return None

	def getHistoryEntry(self):
		if self.is_navigating_history:
			return None
		memory_addr = self.memory_editor.getCurrentOffset()
		if memory_addr != self.memory_history_addr:
			self.memory_history_addr = memory_addr
		if self.is_raw_disassembly and self.debug_state.connected:
			address = self.raw_address
			module = self.debug_state.modules.get_module_for_addr(address)
			modstart = self.debug_state.modules[module]
			relative_address = address - modstart
			return DebugView.DebugViewHistoryEntry(memory_addr, (module, relative_address), True)
		else:
			address = self.binary_editor.getDisassembly().getCurrentOffset()
			return DebugView.DebugViewHistoryEntry(memory_addr, address, False)

	def navigateToHistoryEntry(self, entry):
		self.is_navigating_history = True
		if hasattr(entry, 'is_raw'):
			self.memory_editor.navigate(entry.memory_addr)
			if entry.is_raw:
				if self.debug_state.connected:
					module, relative_address = entry.address
					address = self.debug_state.modules[module] + relative_address
					self.navigate_raw(address)
			else:
				self.navigate_live(entry.address)

		View.navigateToHistoryEntry(self, entry)
		self.is_navigating_history = False

	def navigate(self, addr):
		if self.debug_state.memory_view.is_local_addr(addr):
			addr = self.debug_state.memory_view.remote_addr_to_local(addr)
			if self.debug_state.bv.read(addr, 1) and len(self.debug_state.bv.get_functions_containing(addr)) > 0:
				return self.navigate_live(addr)
		return self.navigate_raw(addr)

	def navigate_live(self, addr):
		self.show_raw_disassembly(False)
		return self.binary_editor.getDisassembly().navigate(addr)

	def navigate_raw(self, addr):
		self.raw_address = addr
		self.show_raw_disassembly(True)
		self.load_raw_disassembly(addr)
		return True

	def navigate_memory(self, addr):
		self.memory_editor.navigate(addr)

	def notifyMemoryChanged(self):
		self.needs_update = True

	def updateTimerEvent(self):
		if self.needs_update:
			self.needs_update = False

			# Refresh the editor
			if not self.debug_state.connected:
				self.memory_editor.navigate(0)
				return

			# self.memory_editor.navigate(self.debug_state.stack_pointer)

	def showEvent(self, event):
		if not event.spontaneous():
			self.update_timer.start()
			self.add_scripting_ref()

	def hideEvent(self, event):
		if not event.spontaneous():
			self.update_timer.stop()

	def shouldBeVisible(self, view_frame):
		if view_frame is None:
			return False
		else:
			return True

	def load_raw_disassembly(self, start_ip):
		# Read a few instructions from rip and disassemble them
		inst_count = 50

		arch_dis = self.debug_state.remote_arch
		rip = self.debug_state.ip

		# Assume the worst, just in case
		read_length = arch_dis.max_instr_length * inst_count
		data = self.debug_state.memory_view.read(start_ip, read_length)

		lines = []

		# Append header line
		tokens = [InstructionTextToken(InstructionTextTokenType.TextToken, "(Code not backed by loaded file, showing only raw disassembly)")]
		contents = DisassemblyTextLine(tokens, start_ip)
		line = LinearDisassemblyLine(LinearDisassemblyLineType.BasicLineType, None, None, 0, contents)
		lines.append(line)

		total_read = 0
		for i in range(inst_count):
			line_addr = start_ip + total_read
			(insn_tokens, length) = arch_dis.get_instruction_text(data[total_read:], line_addr)

			if insn_tokens is None:
				insn_tokens = [InstructionTextToken(InstructionTextTokenType.TextToken, "??")]
				length = arch_dis.instr_alignment
				if length == 0:
					length = 1

			tokens = []
			color = HighlightStandardColor.NoHighlightColor
			if line_addr == rip:
				if self.debug_state.breakpoints.contains_absolute(start_ip + total_read):
					# Breakpoint & pc
					tokens.append(InstructionTextToken(InstructionTextTokenType.TagToken, self.get_breakpoint_tag_type().icon + ">", width=5))
					color = HighlightStandardColor.RedHighlightColor
				else:
					# PC
					tokens.append(InstructionTextToken(InstructionTextTokenType.TextToken, " ==> "))
					color = HighlightStandardColor.BlueHighlightColor
			else:
				if self.debug_state.breakpoints.contains_absolute(start_ip + total_read):
					# Breakpoint
					tokens.append(InstructionTextToken(InstructionTextTokenType.TagToken, self.get_breakpoint_tag_type().icon, width=5))
					color = HighlightStandardColor.RedHighlightColor
				else:
					# Regular line
					tokens.append(InstructionTextToken(InstructionTextTokenType.TextToken, "     "))
			# Address
			tokens.append(InstructionTextToken(InstructionTextTokenType.AddressDisplayToken, hex(line_addr)[2:], line_addr))
			tokens.append(InstructionTextToken(InstructionTextTokenType.TextToken, "  "))
			tokens.extend(insn_tokens)

			# Convert to linear disassembly line
			contents = DisassemblyTextLine(tokens, line_addr, color=color)
			line = LinearDisassemblyLine(LinearDisassemblyLineType.CodeDisassemblyLineType, None, None, 0, contents)
			lines.append(line)

			total_read += length

		# terrible workaround for libshiboken conversion issue
		for line in lines:
			# line is LinearDisassemblyLine
			last_tok = line.contents.tokens[-1]
			#if last_tok.type != InstructionTextTokenType.PossibleAddressToken: continue
			#if last_tok.width != 18: continue # strlen("0xFFFFFFFFFFFFFFF0")
			if last_tok.size != 8: continue
			#print('fixing: %s' % line)
			last_tok.value &= 0x7FFFFFFFFFFFFFFF

		self.binary_text.setLines(lines)

	def show_raw_disassembly(self, raw):
		if raw != self.is_raw_disassembly:
			self.splitter.replaceWidget(0, self.disasm_widget if raw else self.bv_widget)
			self.is_raw_disassembly = raw

class DebugViewType(ViewType):
	def __init__(self):
		super(DebugViewType, self).__init__("Debugger", "Debugger")

	def getPriority(self, data, filename):
		return 1

	def create(self, data, view_frame):
		return DebugView(view_frame, data)

