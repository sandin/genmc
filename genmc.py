# genmc - Display Hex-Rays Microcode
#
# Requires IDA and decompiler(s) >= 7.3
#
# Based on code/ideas from:
# - vds13.py from Hexrays SDK
# - https://github.com/RolfRolles/HexRaysDeob
# - https://github.com/NeatMonster/MCExplorer

__author__ = "Dennis Elser"

# -----------------------------------------------------------------------------
import os, shutil, errno, re

import ida_idaapi
import ida_bytes
import ida_range
import ida_kernwin as kw
import ida_hexrays as hr
import ida_funcs
import ida_diskio
import ida_ida
import ida_graph
import ida_lines
import ida_moves

PLUGIN_NAME = "genmc"

# -----------------------------------------------------------------------------
def get_mcode_name(mcode):
    """returns the name of the mcode_t passed in parameter."""
    for x in dir(hr):
        if x.startswith('m_'):
            if mcode == getattr(hr, x):
                return x
    return None

# -----------------------------------------------------------------------------
def get_mopt_name(mopt):
    """returns the name of the mopt_t passed in parameter."""
    for x in dir(hr):
        if x.startswith('mop_'):
            if mopt == getattr(hr, x):
                return x
    return None

# -----------------------------------------------------------------------------
def is_plugin():
    """returns True if this script is executed from within an IDA plugins
    directory, False otherwise."""
    return "__plugins__" in __name__

# -----------------------------------------------------------------------------
def get_target_filename():
    """returns destination path for plugin installation."""
    return os.path.join(
        ida_diskio.get_user_idadir(),
        "plugins",
        "%s%s" % (PLUGIN_NAME, ".py"))

# -----------------------------------------------------------------------------
def is_installed():
    """checks whether script is present in designated plugins directory."""
    return os.path.isfile(get_target_filename())

# -----------------------------------------------------------------------------
SELF = __file__
def install_plugin():
    """Installs script to IDA userdir as a plugin."""
    if is_plugin():
        kw.msg("Command not available. Plugin already installed.\n")
        return False

    src = SELF
    if is_installed():
        btnid = kw.ask_yn(kw.ASKBTN_NO,
            "File exists:\n\n%s\n\nReplace?" % get_target_filename())
        if btnid is not kw.ASKBTN_YES:
            return False
    dst = get_target_filename()
    usrdir = os.path.dirname(dst)
    kw.msg("Copying script from \"%s\" to \"%s\" ..." % (src, usrdir))
    if not os.path.exists(usrdir):
        try:
            os.path.makedirs(usrdir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                kw.msg("failed (mkdir)!\n")
                return False
    try:
        shutil.copy(src, dst)
    except:
        kw.msg("failed (copy)!\n")
        return False
    kw.msg(("done\n"
        "Plugin installed - please restart this IDA instance now.\n"))
    return True

# -----------------------------------------------------------------------------
def is_ida_version(requested):
    """Checks minimum required IDA version."""
    rv = requested.split(".")
    kv = kw.get_kernel_version().split(".")

    count = min(len(rv), len(kv))
    if not count:
        return False

    for i in range(count):
        if int(kv[i]) < int(rv[i]):
            return False
    return True

# -----------------------------------------------------------------------------
def is_compatible():
    """Checks whether script is compatible with current IDA and
    decompiler versions."""
    min_ida_ver = "7.3"
    return is_ida_version(min_ida_ver) and hr.init_hexrays_plugin()

# -----------------------------------------------------------------------------
class printer_t(hr.vd_printer_t):
    """Converts microcode output to an array of strings."""
    def __init__(self, *args):
        hr.vd_printer_t.__init__(self)
        self.mc = []

    def get_mc(self):
        return self.mc

    def _print(self, indent, line):
        self.mc.append(line)
        return 1

# -----------------------------------------------------------------------------
class microcode_insnviewer_t(ida_graph.GraphViewer):
    """Displays the graph view of Hex-Rays microcode."""
    def __init__(self, mba, mmat_name, fn_name, block, serial):
        title = "Microinstruction: %s - %d.%d (%s)" % (fn_name, block, serial, mmat_name)
        ida_graph.GraphViewer.__init__(self, title, True)
        self.mblock = mba.get_mblock(block)
        self.minsn = self.get_minsn(serial)
    
    def get_minsn(self, serial):
        curr = self.mblock.head
        for i in range(serial):
            curr = curr.next
        return curr

    def _insert_mop(self, mop, parent):
        if mop.t == 0:
            return -1

        text = get_mopt_name(mop.t) + '\n' + mop._print()
        node_id = self.AddNode(text)
        self.AddEdge(parent, node_id)

        if mop.t == hr.mop_d: # result of another instruction
            dst_id = self._insert_minsn(mop.d)
            if dst_id:
                self.AddEdge(node_id, dst_id)
        elif mop.t == hr.mop_f: # list of arguments
            for arg in mop.f.args:
                self._insert_mop(arg, node_id)
        elif mop.t == hr.mop_a: # mop_addr_t: address of operand
            self._insert_mop(mop.a, node_id)
        elif mop.t == hr.mop_p: # operand pair
            self._insert_mop(mop.pair.lop, node_id)
            self._insert_mop(mop.pair.hop, node_id)
        return node_id

    def _insert_minsn(self, minsn):
        if minsn:
            text = get_mcode_name(minsn.opcode) + '\n' + minsn._print()
            node_id = self.AddNode(text)
            
            self._insert_mop(minsn.l, node_id)
            self._insert_mop(minsn.r, node_id)
            self._insert_mop(minsn.d, node_id)
            return node_id
        return None

    def OnRefresh(self):
        self.Clear()
        self._insert_minsn(self.minsn)
        return True

    def OnGetText(self, node_id):
        return self._nodes[node_id]

# -----------------------------------------------------------------------------
class microcode_graphviewer_t(ida_graph.GraphViewer):
    """Displays the graph view of Hex-Rays microcode."""
    def __init__(self, mba, title, lines):
        title = "Microcode graph: %s" % title
        ida_graph.GraphViewer.__init__(self, title, True)
        self._mba = mba
        self._mba.set_mba_flags(hr.MBA_SHORT)
        self._process_lines(lines)
        if mba.maturity == hr.MMAT_GENERATED or mba.maturity == hr.MMAT_PREOPTIMIZED:
            mba.build_graph()
    
    def _process_lines(self, lines):
        self._blockcmts = {}
        curblk = "-1"
        self._blockcmts[curblk] = []
        for i, line in enumerate(lines):
            plain_line = ida_lines.tag_remove(line).lstrip()
            if plain_line.startswith(';'):
                # msg("%s" % plain_line)
                re_ret = re.findall("BLOCK ([0-9]+) ", plain_line)
                if len(re_ret) > 0:
                    curblk = re_ret[0]
                    self._blockcmts[curblk] = [line]
                else:
                    self._blockcmts[curblk].append(line)
        if "0" in self._blockcmts:
            self._blockcmts["0"] = self._blockcmts["-1"] + self._blockcmts["0"]
        del self._blockcmts["-1"]

    def OnRefresh(self):
        self.Clear()
        qty = self._mba.qty
        for src in range(qty):
            self.AddNode(src)
        for src in range(qty):
            mblock = self._mba.get_mblock(src)
            for dest in mblock.succset:
                self.AddEdge(src, dest)
        return True

    def OnGetText(self, node):
        mblock = self._mba.get_mblock(node)
        vp = hr.qstring_printer_t(None, True)
        mblock._print(vp)
        
        node_key = "%d" % node
        if node_key in self._blockcmts:
            return ''.join(self._blockcmts[node_key]) + vp.s
        else:
            return vp.s

# -----------------------------------------------------------------------------
class microcode_viewer_t(kw.simplecustviewer_t):
    """Creates a widget that displays Hex-Rays microcode."""
    def Create(self, mba, title, mmat_name, fn_name, lines = []):
        self.title = "Microcode: %s" % title
        self._mba = mba
        self.mmat_name = mmat_name
        self.fn_name = fn_name
        if not kw.simplecustviewer_t.Create(self, self.title):
            return False
        self.lines = lines
        for line in lines:
            self.AddLine(line)
        return True

    def _fit_graph(self, graph):
        if graph:
            gv = graph.GetWidget()
            ida_graph.viewer_fit_window(gv)
            ida_graph.refresh_viewer(gv)
            return True
        return False

    def _dock_widgets(self, graph, dockpos=kw.DP_RIGHT):
        if graph:
            gv = graph.GetWidget()
            kw.set_dock_pos(kw.get_widget_title(gv), self.title, dockpos)

            gli = ida_moves.graph_location_info_t()
            if ida_graph.viewer_get_gli(gli, gv):
                gli.zoom = 0 # auto-position
                ida_graph.viewer_set_gli(gv, gli, ida_graph.GLICTL_CENTER)
                ida_graph.refresh_viewer(gv)
            return True
        return False

    """TODO: it's better to handle keyboard input by
    registering an "action" and assigning it a hotkey"""
    def OnKeydown(self, vkey, shift):
        if vkey == ord("G"):
            g = microcode_graphviewer_t(self._mba, self.title, self.lines)
            if g:
                g.Show()
                self._fit_graph(g)
                self._dock_widgets(g,
                    dockpos=kw.DP_FLOATING if shift else kw.DP_RIGHT)
            return True
        elif vkey == ord("I"):
            """TODO: at some point, the textual representation of the mba
                 should manually be created.
              -> we would no longer have to parse the textual output
                 that is created by the gen_microcode() function
              .> we may insert COLOR_ADDR tags which would allow us to
                 contextually link different viewers"""
            widget = self.GetWidget()
            line = kw.get_custom_viewer_curline(widget, False)
            line = ida_lines.tag_remove(line)
            p = line.find(" ")
            if p != -1 and '.' in line[:p]:
                block, serial = line.split('.')[:2]
                serial = serial.strip().split(' ')[0]
                g = microcode_insnviewer_t(self._mba, self.mmat_name, self.fn_name, int(block), int(serial))
                if g:
                    g.Show()
                    self._fit_graph(g)
                    self._dock_widgets(g,
                        dockpos=kw.DP_FLOATING if shift else kw.DP_TAB)
            else:
                message = ("There is something wrong with the output generated by gen_microcode()!\n"
                    "Please rerun '%s.py'!" % PLUGIN_NAME)
                if line.startswith(";") or not(len(line)):
                    message = "Please position the cursor on a microcode instruction."
                kw.warning(message)
            return True
        return False

    def OnDblClick(self, shift):
        word = self.GetCurrentWord()
        ea = int(word, base=16)
        if ea > 0:
            kw.jumpto(ea)
        #print("OnDblClick, shift=%d, current ea=%s" % (shift, hex(ea)))
        return True
    
# -----------------------------------------------------------------------------
def ask_desired_maturity():
    """Displays a dialog which lets the user choose a maturity level
    of the microcode to generate."""
    maturity_levels = [
    ["MMAT_GENERATED", hr.MMAT_GENERATED],
    ["MMAT_PREOPTIMIZED", hr.MMAT_PREOPTIMIZED],
    ["MMAT_LOCOPT", hr.MMAT_LOCOPT],
    ["MMAT_CALLS", hr.MMAT_CALLS],
    ["MMAT_GLBOPT1", hr.MMAT_GLBOPT1],
    ["MMAT_GLBOPT2", hr.MMAT_GLBOPT2],
    ["MMAT_GLBOPT3", hr.MMAT_GLBOPT3],
    ["MMAT_LVARS", hr.MMAT_LVARS]]

    class MaturityForm(kw.Form):
        def __init__(self):
            self.title = "Display Microcode"
            form = ("STARTITEM {id:mat_lvl}\n"
                "%s\n"
                " \n"
                "<Maturity level:{mat_lvl}>\n\n"
                "<##Options##Output includes comments:{flags_short}>{chkgroup_flags}>\n\n" %
                self.title)

            dropdown_ctl = kw.Form.DropdownListControl(
                [text for text, _ in maturity_levels])
            chk_ctl = kw.Form.ChkGroupControl(("flags_short",))

            controls = {"mat_lvl": dropdown_ctl,
            "chkgroup_flags": chk_ctl}

            kw.Form.__init__(self, form, controls)

    form = MaturityForm()
    form, args = form.Compile()
    form.flags_short.checked = True
    ok = form.Execute()

    mmat = None
    text = None
    flags = 0
    if ok == 1:
        text, mmat = maturity_levels[form.mat_lvl.value]
    flags |= 0 if form.flags_short.checked else hr.MBA_SHORT
    form.Free()
    return (text, mmat, flags)

# -----------------------------------------------------------------------------
def show_microcode():
    """Generates and displays microcode for an address range.
    An address range can be a selection of code or that of
    the current function."""
    sel, sea, eea = kw.read_range_selection(None)
    pfn = ida_funcs.get_func(kw.get_screen_ea())
    if not sel and not pfn:
        return (False, "Position cursor within a function or select range")

    if not sel and pfn:
        sea = pfn.start_ea
        eea = pfn.end_ea

    addr_fmt = "%016x" if ida_ida.inf_is_64bit() else "%08x"
    fn_name = (ida_funcs.get_func_name(pfn.start_ea)
        if pfn else "0x%s-0x%s" % (addr_fmt % sea, addr_fmt % eea))
    F = ida_bytes.get_flags(sea)
    if not ida_bytes.is_code(F):
        return (False, "The selected range must start with an instruction")

    text, mmat, mba_flags = ask_desired_maturity()
    if text is None and mmat is None:
        return (True, "Cancelled")
    
    if not sel and pfn:
        mbr = hr.mba_ranges_t(pfn)
    else:
        mbr = hr.mba_ranges_t()
        mbr.ranges.push_back(ida_range.range_t(sea, eea))

    hf = hr.hexrays_failure_t()
    ml = hr.mlist_t()
    mba = hr.gen_microcode(mbr, hf, ml, hr.DECOMP_WARNINGS, mmat)
    if not mba:
        return (False, "0x%s: %s" % (addr_fmt % hf.errea, hf.desc()))
    vp = printer_t()
    mba.set_mba_flags(mba_flags)
    mba._print(vp)
    mcv = microcode_viewer_t()
    if not mcv.Create(mba, "0x%s-0x%s (%s)" % (addr_fmt % sea, addr_fmt % eea, text), text, fn_name, vp.get_mc()):
        return (False, "Error creating viewer")

    mcv.Show()
    return (True,
        "Successfully generated microcode for 0x%s..0x%s" % (addr_fmt % sea, addr_fmt % eea))

# -----------------------------------------------------------------------------
def create_mc_widget():
    """Checks minimum requirements for the script/plugin to be able to run.
    Displays microcode or in case of failure, displays error message.
    This function acts as the main entry point that is invoked if the
    code is run as a script or as a plugin."""
    if not is_compatible():
        kw.msg("%s: Unsupported IDA / Hex-rays version\n" % (PLUGIN_NAME))
        return False
    success, message = show_microcode()
    output = kw.msg if success else kw.warning
    output("%s: %s\n" % (PLUGIN_NAME, message))
    return success

# -----------------------------------------------------------------------------
class genmc(ida_idaapi.plugin_t):
    """Class that is required for the code to be recognized as
    a plugin by IDA."""
    flags = 0
    comment = "Display microcode"
    help = comment
    wanted_name = PLUGIN_NAME
    wanted_hotkey = "Ctrl-Shift-M"

    def init(self):
        return (ida_idaapi.PLUGIN_OK if
            is_compatible() else ida_idaapi.PLUGIN_SKIP)

    def run(self, arg):
        create_mc_widget()

    def term(self):
        pass

# -----------------------------------------------------------------------------
def PLUGIN_ENTRY():
    """Entry point of this code if launched as a plugin."""
    return genmc()

# -----------------------------------------------------------------------------
def SCRIPT_ENTRY():
    """Entry point of this code if launched as a script."""
    if not is_plugin():
        if not is_installed():
            kw.msg(("%s: Available commands:\n"
                "[+] \"install_plugin()\" - install script to ida_userdir/plugins\n") % (
                PLUGIN_NAME))
        create_mc_widget()
        return True
    return False

# -----------------------------------------------------------------------------
SCRIPT_ENTRY()
