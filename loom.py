# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
import re
import os
import errno
import subprocess
import blend_render_info
import rna_keymap_ui
import webbrowser

from bpy_extras.io_utils import ImportHelper, ExportHelper
from bl_operators.presets import AddPresetBase
from bl_ui.utils import PresetPanel
from numpy import arange, around, isclose
from itertools import count, groupby
from time import strftime
from sys import platform


bl_info = {
    "name": "Loom",
    "description": "Image sequence rendering, encoding and playback",
    "author": "Christian Brinkmann (p2or)",
    "version": (0, 8, 9),
    "blender": (2, 82, 0),
    "doc_url": "https://github.com/p2or/blender-loom",
    "tracker_url": "https://github.com/p2or/blender-loom/issues",
    "support": "COMMUNITY",
    "category": "Render"
}


# -------------------------------------------------------------------
#    Helper
# -------------------------------------------------------------------

def filter_frames(frame_input, increment=1, filter_individual=False):
    """ Filter frame input & convert it to a set of frames """
    def float_filter(st):
        try:
            return float(st)
        except ValueError:
            return None

    def int_filter(flt):
        try:
            return int(flt) if flt.is_integer() else None
        except ValueError:
            return None

    numeric_pattern = r"""
        [\^\!]? \s*? # Exclude option
        [-+]?        # Negative or positive number 
        (?:
            # Range & increment 1-2x2, 0.0-0.1x.02
            (?: \d* \.? \d+ \s? \- \s? \d* \.? \d+ \s? [x%] \s? [-+]? \d* \.? \d+ )
            |
            # Range 1-2, 0.0-0.1 etc
            (?: \d* \.? \d+ \s? \- \s? [-+]? \d* \.? \d+ )
            |
            # .1 .12 .123 etc 9.1 etc 98.1 etc
            (?: \d* \. \d+ )
            |
            # 1. 12. 123. etc 1 12 123 etc
            (?: \d+ \.? )
        )
        """
    range_pattern = r"""
        ([-+]? \d*? \.? [0-9]+ \b) # Start frame
        (\s*? \- \s*?)             # Minus
        ([-+]? \d* \.? [0-9]+)     # End frame
        ( (\s*? [x%] \s*? )( [-+]? \d* \.? [0-9]+ \b ) )? # Increment
        """
    exclude_pattern = r"""
        [\^\!] \s*?             # Exclude option
        ([-+]? \d* \.? \d+)$    # Int or Float
        """

    rx_filter = re.compile(numeric_pattern, re.VERBOSE)
    rx_group = re.compile(range_pattern, re.VERBOSE)
    rx_exclude = re.compile(exclude_pattern, re.VERBOSE)

    input_filtered = rx_filter.findall(frame_input)
    if not input_filtered: return None

    """ Option to add a ^ or ! at the beginning to exclude frames """
    if not filter_individual:
        first_exclude_item = next((i for i, v in enumerate(input_filtered) if "^" in v or "!" in v), None)
        if first_exclude_item:
            input_filtered = input_filtered[:first_exclude_item] + \
                             [elem if elem.startswith(("^", "!")) else "^" + elem.lstrip(' ') \
                              for elem in input_filtered[first_exclude_item:]]

    """ Find single values as well as all ranges & compile frame list """
    frame_list, exclude_list, conform_list  = [], [], []

    conform_flag = False
    for item in input_filtered:
        frame = float_filter(item)
        
        if frame is not None: # Single floats
            frame_list.append(frame)
            if conform_flag: conform_list.append(frame)

        else:  # Ranges & items to exclude
            exclude_item = rx_exclude.search(item)
            range_item = rx_group.search(item)

            if exclude_item:  # Single exclude items like ^-3 or ^10
                exclude_list.append(float_filter(exclude_item.group(1)))
                if filter_individual: conform_flag = True

            elif range_item:  # Ranges like 1-10, 20-10, 1-3x0.1, ^2-7 or ^-3--1
                start = min(float_filter(range_item.group(1)), float_filter(range_item.group(3)))
                end = max(float_filter(range_item.group(1)), float_filter(range_item.group(3)))
                step = increment if not range_item.group(4) else float_filter(range_item.group(6))

                if start < end:  # Build the range & add all items to list
                    frame_range = around(arange(start, end, step), decimals=5).tolist()
                    if item.startswith(("^", "!")):
                        if filter_individual: conform_flag = True
                        exclude_list.extend(frame_range)
                        if isclose(step, (end - frame_range[-1])):
                            exclude_list.append(end)
                    else:
                        frame_list.extend(frame_range)
                        if isclose(step, (end - frame_range[-1])):
                            frame_list.append(end)

                        if conform_flag:
                            conform_list.extend(frame_range)
                            if isclose(step, (end - frame_range[-1])):
                                conform_list.append(end)

                elif start == end:  # Not a range, add start frame
                    if not item.startswith(("^", "!")):
                        frame_list.append(start)
                    else:
                        exclude_list.append(start)

    if filter_individual:
        exclude_list = sorted(set(exclude_list).difference(conform_list))
    float_frames = sorted(set(frame_list).difference(exclude_list))

    """ Return integers whenever possible """
    int_frames = [int_filter(frame) for frame in float_frames]
    return float_frames if None in int_frames else int_frames


def version_number(file_path, number, delimiter="_", min_lead=2):
    """Replace or add a version string by given number"""
    match = re.search(r'v(\d+)', file_path)
    if match:
        g = match.group(1)
        n = str(int(number)).zfill(len(g))
        return file_path.replace(match.group(0), "v{v}".format(v=n))

    else:
        lead_zeros = str(int(number)).zfill(min_lead)
        version = "{dl}v{lz}{dl}".format(dl=delimiter, lz=lead_zeros)
        ext = (".png",".jpg",".jpeg","jpg",".exr",".dpx",".tga",".tif",".tiff",".cin")

        if "#" in file_path:
            dash = file_path.find("#")
            head, tail = file_path[:dash], file_path[dash:]
            if head.endswith(delimiter):
                head = head.rstrip(delimiter)
            return "{h}{v}{t}".format(h=head, v=version, t=tail)

        elif file_path.endswith(ext):
            head, extension = os.path.splitext(file_path)
            if head.endswith(delimiter):
                head = head.rstrip(delimiter)
            return "{fp}{v}{ex}".format(fp=head, v=version[:-1], ex=extension)

        else:
            if file_path.endswith(delimiter):
                file_path = file_path.rstrip(delimiter)
            return "{fp}{v}".format(fp=file_path, v=version)


def render_version(self, context):
    context.area.tag_redraw()
    scene = context.scene
    render = scene.render

    """ Replace the render path """
    render.filepath = version_number(
            render.filepath, 
            scene.loom.output_render_version)

    """ Replace file output """
    if not scene.render.use_compositing or \
        not scene.loom.output_sync_comp or \
        not scene.node_tree:
        return

    output_nodes = [n for n in scene.node_tree.nodes if n.type=='OUTPUT_FILE']
    for out_node in output_nodes:
        """ Set base path only """
        if "LAYER" in out_node.format.file_format:
            out_node.base_path = version_number(
                out_node.base_path, 
                scene.loom.output_render_version)
        else:
            """ Set the base path """
            out_node.base_path = version_number(
                out_node.base_path, 
                scene.loom.output_render_version)
            """ Set all slots """
            for out_file in out_node.file_slots:
                out_file.path = version_number(
                    out_file.path, 
                    scene.loom.output_render_version)
    return None


def isevaluable(s):
    try:
        eval(s)
        return True
    except:
        return False

def replace_globals(s, debug=False):
    """Replace string by given global entries"""
    vars = bpy.context.preferences.addons[__name__].preferences.global_variable_coll
    for key, val in vars.items():
        if not debug:
            if key.startswith("$") and not key.isspace():
                if val.expr and not val.expr.isspace():
                    if isevaluable(val.expr):
                        s = s.replace(key, str(eval(val.expr)))
                    else:
                        s = s.replace(key, "NO-{}".format(key.replace("$", "")))
        else: 
            print (key, val, val.expr)
    return s

def user_globals(context):
    """Determine whether globals used in the scene"""
    scn = context.scene
    vars = context.preferences.addons[__name__].preferences.global_variable_coll
    if any(ext in scn.render.filepath for ext in vars.keys()):
        return True
    if scn.use_nodes and len(scn.node_tree.nodes) > 0:
        tree = scn.node_tree
        nodes = (n for n in tree.nodes if n.type=='OUTPUT_FILE')
        for node in nodes:
            if any(ext in node.base_path for ext in vars.keys()):
                return True
            if "LAYER" in node.format.file_format:
                for slot in node.layer_slots:
                    if any(ext in slot.name for ext in vars.keys()):
                        return True
            else:
                for slot in node.file_slots:
                     if any(ext in slot.path for ext in vars.keys()):
                         return True
    return False


def verify_app(cmd):
    """Verify whether an external app is callable"""
    try:
        subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return False
    return True


# -------------------------------------------------------------------
#    Preferences & Scene Properties
# -------------------------------------------------------------------

class LOOM_PG_globals(bpy.types.PropertyGroup):
    # name: bpy.props.StringProperty()
    expr: bpy.props.StringProperty(name="Python Expression")

class LOOM_UL_globals(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.2)
        eval_icon = 'FILE_SCRIPT' if isevaluable(item.expr) else 'ERROR'
        var_icon = 'RADIOBUT_ON' if item.name.startswith("$") else 'RADIOBUT_OFF'
        split.prop(item, "name", text="", emboss=False, translate=False, icon=var_icon)
        split.prop(item, "expr", text="", emboss=True, translate=False, icon=eval_icon)
    def invoke(self, context, event):
        pass


class LOOM_PG_project_directories(bpy.types.PropertyGroup):
    # name: bpy.props.StringProperty()
    creation_flag: bpy.props.BoolProperty()

class LOOM_UL_directories(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.05)
        split.label(text="{:02d}".format(index+1))
        row = split.row(align=True)
        row.prop(item, "name", text="", icon='FILE_FOLDER') # emboss=False
        row.prop(item, "creation_flag", text="", icon='RADIOBUT_ON' if item.creation_flag else 'RADIOBUT_OFF')#, emboss=False)
    def invoke(self, context, event):
        pass


class LOOM_AP_preferences(bpy.types.AddonPreferences):

    bl_idname = __name__

    terminal: bpy.props.EnumProperty(
        name="Terminal",
        items=(
            ("win-default", "Windows Default Terminal", "", 1),
            ("osx-default", "OSX Default Terminal", "", 2),
            ("x-terminal-emulator", "X Terminal Emulator", "", 3),
            ("xfce4-terminal", "Xfce4 Terminal", "", 4),
            ("xterm", "xterm", "", 5)))

    xterm_flag: bpy.props.BoolProperty(
        name="Use Xterm (Terminal Fallback)",
        description="Serves as fallback for OSX and others",
        default=False)
        
    bash_file: bpy.props.StringProperty(
        name="Bash file",
        description = "Filepath to temporary bash or bat file")

    bash_flag: bpy.props.BoolProperty(
        name="Force Bash File",
        description="Force using bash file instead of individual arguments",
        default=False)

    render_dialog_width: bpy.props.IntProperty(
        name="Render Dialog Width",
        description = "Width of Image Sequence Render Dialog",
        subtype='PIXEL',
        default=450, min=400)

    encode_dialog_width: bpy.props.IntProperty(
        name="Encoding/Rename Dialog Width",
        description = "Width of Encoding and Rename Dialog",
        subtype='PIXEL',
        default=650, min=400)

    project_dialog_width: bpy.props.IntProperty(
        name="Project Dialog Width",
        description = "Width of Project Dialog",
        subtype='PIXEL',
        default=650, min=400)

    timeline_extensions: bpy.props.BoolProperty(
        name="Timeline Extensions",
        description="Do not display Loom operators in the Timeline",
        default=False)

    output_extensions: bpy.props.BoolProperty(
        name="Output Panel Extensions",
        description="Do not display all File Output nodes and the final Output Path in the Output Panel",
        default=False)

    log_render: bpy.props.BoolProperty(
        name="Logging (Required for Playback)",
        description="If enabled render output properties will be saved",
        default=True)

    log_render_limit: bpy.props.IntProperty(
        name="Log Limit",
        default=3)

    playblast_flag: bpy.props.BoolProperty(
        name="Playblast (Experimental)",
        description="Playback rendered sequences",
        default=False)
    
    user_player: bpy.props.BoolProperty(
        name="Default Animation Player",
        description="Use default player (User Preferences > File Paths)",
        default=False)

    ffmpeg_path: bpy.props.StringProperty(
        name="FFmpeg Binary",
        description="Path to ffmpeg",
        maxlen=1024,
        subtype='FILE_PATH')
    
    snapshot_directory: bpy.props.StringProperty(
        name="Snapshot Directory",
        description="Path of the Snapshot directory",
        maxlen=1024,
        default="//temp",
        subtype='DIR_PATH')

    default_codec: bpy.props.StringProperty(
        name="User Codec",
        description = "Default user codec")

    batch_dialog_width: bpy.props.IntProperty(
        name="Batch Dialog Width",
        description="Width of Batch Render Dialog",
        subtype='PIXEL',
        default=750, min=600, max=1800)

    batch_dialog_rows: bpy.props.IntProperty(
        name="Number of Rows",
        description="Number of Rows",
        min=7, max=40,
        default=9)
    
    batch_paths_flag: bpy.props.BoolProperty(
        name="Display File Paths",
        description="Display File paths")

    batch_path_col_width: bpy.props.FloatProperty(
        name="Path Column Width",
        description="Width of path column in list",
        default=0.6, min=0.3, max=0.8)

    batch_name_col_width: bpy.props.FloatProperty(
        name="Name Column Width",
        description="Width of name column in list",
        default=0.45, min=0.3, max=0.8)

    render_background: bpy.props.BoolProperty(
        name="Render in Background",
        description="Do not activate the Console",
        default=False)

    global_variable_coll: bpy.props.CollectionProperty(
        name="Global Variables",
        type=LOOM_PG_globals)
    
    global_variable_idx: bpy.props.IntProperty(
        name="Index",
        default=0)
    
    expression: bpy.props.StringProperty(
        name="Expression",
        description = "Test Expression",
        options={'SKIP_SAVE'})
    
    project_directory_coll: bpy.props.CollectionProperty(
        name="Project Folders",
        type=LOOM_PG_project_directories)
    
    project_coll_idx: bpy.props.IntProperty(
        name="Index",
        default=0)

    display_general: bpy.props.BoolProperty(
        default=True)

    display_globals: bpy.props.BoolProperty(
        default=False)

    display_directories: bpy.props.BoolProperty(
        default=False)

    display_presets: bpy.props.BoolProperty(
        default=False)

    display_advanced: bpy.props.BoolProperty(
        default=False)

    display_hotkeys: bpy.props.BoolProperty(
        default=True)
    
    render_presets_path: bpy.props.StringProperty(
        subtype = "FILE_PATH",
        default = bpy.utils.user_resource(
            'SCRIPTS',
            path=os.path.join("presets", "loom/render_presets")))

    def draw_state(self, prop):
        return 'RADIOBUT_OFF' if not prop else 'RADIOBUT_ON'

    def draw(self, context):
        split_width = 0.5
        layout = self.layout

        """ General """
        box_general = layout.box()
        row = box_general.row()
        row.prop(self, "display_general",
            icon="TRIA_DOWN" if self.display_general else "TRIA_RIGHT",
            icon_only=True, emboss=False)
        row.label(text="General")
        
        if self.display_general:
            split = box_general.split(factor=split_width)
            col = split.column()
            col.prop(self, "render_dialog_width")
            col.prop(self, "batch_dialog_width")
            col = split.column()
            col.prop(self, "project_dialog_width")
            col.prop(self, "encode_dialog_width")

            split = box_general.split(factor=split_width)
            col = split.column()
            col.prop(self, "timeline_extensions", toggle=True, icon=self.draw_state(not self.timeline_extensions))
            col.prop(self, "output_extensions", toggle=True, icon=self.draw_state(not self.output_extensions))
            col = split.column()
            col.prop(self, "playblast_flag", toggle=True, icon=self.draw_state(self.playblast_flag))
            upl = col.column()
            upl.prop(self, "user_player", toggle=True, icon=self.draw_state(self.user_player))
            upl.enabled = self.playblast_flag

            box_general.row().prop(self, "ffmpeg_path")
            box_general.row()

        """ Globals """
        box_globals = layout.box()
        row = box_globals.row()
        row.prop(self, "display_globals",
            icon="TRIA_DOWN" if self.display_globals else "TRIA_RIGHT",
            icon_only=True, emboss=False)
        row.label(text="Globals (File Output)")

        if self.display_globals:
            row = box_globals.row()
            row.template_list(
                listtype_name = "LOOM_UL_globals", 
                list_id = "", 
                dataptr = self, 
                propname = "global_variable_coll", 
                active_dataptr = self, 
                active_propname = "global_variable_idx", 
                rows=6)
            col = row.column(align=True)
            col.operator(LOOM_OT_globals_ui.bl_idname, icon='ADD', text="").action = 'ADD'
            col.operator(LOOM_OT_globals_ui.bl_idname, icon='REMOVE', text="").action = 'REMOVE'
            col.separator()
            col.operator("wm.save_userpref", text="", icon="CHECKMARK")
            #row = box_globals.row()
            #row.operator("wm.save_userpref", text="Save Globals", icon="CHECKMARK")
            col.separator()
            exp_box = box_globals.box()
            row = exp_box.row()
            row.label(text='Expression Tester')
            row = exp_box.row()
            split = row.split(factor=0.2)
            split.label(text="Expression:", icon='FILE_SCRIPT')
            split.prop(self, "expression", text="")
            if not self.expression or self.expression.isspace():
                eval_info = "Nothing to evaluate"
            else:
                eval_info = eval(self.expression) if isevaluable(self.expression) else "0"
            row = exp_box.row()
            split = row.split(factor=0.2)
            split.label(text="Result:", icon='FILE_VOLUME')
            split.label(text="{}".format(eval_info))
            box_globals.row()
            
        """ Project Directories """
        box_dirs = layout.box()
        row = box_dirs.row()
        row.prop(self, "display_directories",
            icon="TRIA_DOWN" if self.display_directories else "TRIA_RIGHT",
            icon_only=True, emboss=False)
        row.label(text="Project Directories")

        if self.display_directories:
            row = box_dirs.row()
            row.template_list(
                listtype_name = "LOOM_UL_directories", 
                list_id = "", 
                dataptr = self, 
                propname = "project_directory_coll", 
                active_dataptr = self, 
                active_propname = "project_coll_idx", 
                rows=6)
            col = row.column(align=True)
            col.operator(LOOM_OT_directories_ui.bl_idname, icon='ADD', text="").action = 'ADD'
            col.operator(LOOM_OT_directories_ui.bl_idname, icon='REMOVE', text="").action = 'REMOVE'
            box_dirs.row()

        """ Advanced """
        box_advanced = layout.box()
        row = box_advanced.row()
        row.prop(self, "display_advanced",
            icon="TRIA_DOWN" if self.display_advanced else "TRIA_RIGHT",
            icon_only=True, emboss=False)
        row.label(text="Advanced Settings")
        
        if self.display_advanced:
            split = box_advanced.split(factor=split_width)

            lft = split.column() # Left
            fsh = lft.column(align=True)
            txt = "Force generating .bat file" if platform.startswith('win32') else "Force generating .sh file"
            lft.prop(self, "bash_flag", text=txt, toggle=True, icon=self.draw_state(self.bash_flag))
    
            rsh = lft.row(align=True)
            txt = "Delete temporary .bat Files" if platform.startswith('win32') else "Delete temporary .sh files"
            rsh.operator(LOOM_OT_delete_bash_files.bl_idname, text=txt, icon="FILE_SCRIPT")
            script_folder = bpy.utils.script_path_user()
            rsh.operator(LOOM_OT_open_folder.bl_idname, icon="DISK_DRIVE", text="").folder_path = script_folder

            rgt = split.column() # Right
            rbg = rgt.column(align=True)
            rbg.prop(self, "render_background", toggle=True, icon=self.draw_state(self.render_background))

            rgt.column(align=True)
            xtm = rgt.row(align=True)
            xtm.prop(self, "xterm_flag", toggle=True, icon=self.draw_state(self.xterm_flag))
            wp = xtm.operator(LOOM_OT_openURL.bl_idname, icon='HELP', text="")
            wp.description = "Open the Wikipedia page about Xterm"
            wp.url = "https://en.wikipedia.org/wiki/Xterm"

            """ Linux/OSX specific properties """
            if platform.startswith('win32'):
                rsh.enabled = False

            """ OSX specific properties """
            if platform.startswith('darwin'):
                fsh.enabled = False
                rbg.enabled = True
            
            box_advanced.row()
            box_advanced.row().prop(self, "snapshot_directory")
            box_advanced.row()
        
        """ Hotkeys """
        box_hotkeys = layout.box()
        row = box_hotkeys.row()
        row.prop(self, "display_hotkeys",
            icon="TRIA_DOWN" if self.display_hotkeys else "TRIA_RIGHT",
            icon_only=True, emboss=False)
        row.label(text="Hotkeys")

        if self.display_hotkeys:
            split = box_hotkeys.split()
            col = split.column()
            kc_usr = bpy.context.window_manager.keyconfigs.user
            km_usr = kc_usr.keymaps.get('Screen')

            if not user_keymap_ids: # Ouch, Todo!
                for kmi_usr in km_usr.keymap_items:
                    for km_addon, kmi_addon in addon_keymaps:
                        if kmi_addon.compare(kmi_usr):
                            user_keymap_ids.append(kmi_usr.id)
            for kmi_usr in km_usr.keymap_items: # user hotkeys by namespace
                if kmi_usr.idname.startswith("loom."):
                    col.context_pointer_set("keymap", km_usr)
                    rna_keymap_ui.draw_kmi([], kc_usr, km_usr, kmi_usr, col, 0)
            box_hotkeys.row()

        """ Reset Prefs """
        layout.operator(LOOM_OT_preferences_reset.bl_idname, icon='RECOVER_LAST')


class LOOM_OT_preferences_reset(bpy.types.Operator):
    """Reset Add-on Preferences"""
    bl_idname = "loom.reset_preferences"
    bl_label = "Reset Loom Preferences"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        props = prefs.__annotations__.keys()
        for p in props:
            prefs.property_unset(p)

        """ Restore Globals """
        for key, value in global_var_defaults.items():
            gvi = prefs.global_variable_coll.add()
            gvi.name = key
            gvi.expr = value
    
        """ Project Directories """
        for key, value in project_directories.items():
            di = prefs.project_directory_coll.add()
            di.name = value
            di.creation_flag = True

        """ Restore default keys by keymap ids """
        kc_usr = context.window_manager.keyconfigs.user
        km_usr = kc_usr.keymaps.get('Screen')
        for i in user_keymap_ids:
            kmi = km_usr.keymap_items.from_id(i)
            if kmi:
                km_usr.restore_item_to_default(kmi)
        
        return {'FINISHED'}


class LOOM_OT_globals_ui(bpy.types.Operator):
    """Move global variables up and down, add and remove"""
    bl_idname = "loom.globals_action"
    bl_label = "Global Actions"
    bl_options = {'REGISTER', 'INTERNAL'}

    action: bpy.props.EnumProperty(
        items=(
            ('REMOVE', "Remove", ""),
            ('ADD', "Add", "")))

    def invoke(self, context, event):
        prefs = context.preferences.addons[__name__].preferences
        idx = prefs.global_variable_idx
        try:
            item = prefs.global_variable_coll[idx]
        except IndexError:
            pass
        else:
            if self.action == 'REMOVE':
                info = 'Item "%s" removed from list' % (prefs.global_variable_coll[idx].name)
                prefs.global_variable_idx -= 1
                prefs.global_variable_coll.remove(idx)
                if prefs.global_variable_idx < 0: prefs.global_variable_idx = 0
                self.report({'INFO'}, info)

        if self.action == 'ADD':
            item = prefs.global_variable_coll.add()
            prefs.global_variable_idx = len(prefs.global_variable_coll)-1
            info = '"%s" added to list' % (item.name)
            self.report({'INFO'}, info)

        return {"FINISHED"}


class LOOM_OT_directories_ui(bpy.types.Operator):
    """Move items up and down, add and remove"""
    bl_idname = "loom.directory_action"
    bl_label = "Directory Actions"
    bl_options = {'REGISTER', 'INTERNAL'}

    action: bpy.props.EnumProperty(
        items=(
            ('REMOVE', "Remove", ""),
            ('ADD', "Add", "")))

    def invoke(self, context, event):
        prefs = context.preferences.addons[__name__].preferences
        idx = prefs.project_coll_idx
        try:
            item = prefs.project_directory_coll[idx]
        except IndexError:
            pass
        else:
            if self.action == 'REMOVE':
                info = 'Item "%s" removed from list' % (prefs.project_directory_coll[idx].name)
                prefs.project_coll_idx -= 1
                prefs.project_directory_coll.remove(idx)
                if prefs.project_coll_idx < 0: prefs.project_coll_idx = 0

        if self.action == 'ADD':
            item = prefs.project_directory_coll.add()
            item.creation_flag = True
            prefs.project_coll_idx = len(prefs.project_directory_coll)-1
        return {"FINISHED"}


def render_preset_callback(scene, context):
    items = [('EMPTY', "Current Render Settings", "")]
    for f in os.listdir(context.preferences.addons[__name__].preferences.render_presets_path):
         if not f.startswith(".") and f.endswith(".py"):
             fn, ext = os.path.splitext(f)
             #d = bpy.path.display_name(os.path.join(rndr_presets_path, f))
             items.append((f, "'{}' Render Preset".format(fn), ""))
    return items


class LOOM_PG_render(bpy.types.PropertyGroup):
    # name: bpy.props.StringProperty()
    render_id: bpy.props.IntProperty()
    start_time: bpy.props.StringProperty()
    start_frame: bpy.props.StringProperty()
    end_frame: bpy.props.StringProperty()
    file_path: bpy.props.StringProperty()
    padded_zeros: bpy.props.IntProperty()
    image_format: bpy.props.StringProperty()


class LOOM_PG_batch_render(bpy.types.PropertyGroup):
    # name: bpy.props.StringProperty()
    rid: bpy.props.IntProperty()
    path: bpy.props.StringProperty()
    frame_start: bpy.props.IntProperty()
    frame_end: bpy.props.IntProperty()
    scene: bpy.props.StringProperty()
    frames: bpy.props.StringProperty(name="Frames")
    encode_flag: bpy.props.BoolProperty(default=False)
    input_filter: bpy.props.BoolProperty(default=False)


class LOOM_PG_preset_flags(bpy.types.PropertyGroup):

    include_engine_settings: bpy.props.BoolProperty(
        name="Engine Settings", # Currently not exposed to the user
        description="Store 'Render Engine' settings",
        default=True)

    include_resolution: bpy.props.BoolProperty(
        name="Resolution",
        description="Store current 'Format' settings")

    include_output_path: bpy.props.BoolProperty(
        name="Output Path",
        description="Store current 'Output Path'")
    
    include_file_format: bpy.props.BoolProperty(
        name="File Format",
        description="Store current 'File Format' settings")
    
    include_scene_settings: bpy.props.BoolProperty(
        name="Scene Settings", 
        description="Store current 'Scene' settings")

    include_passes: bpy.props.BoolProperty(
        name="Passes",
        description="Store current 'Passes' settings")

    include_color_management: bpy.props.BoolProperty(
        name="Color Management",
        description="Store current 'Color Management' settings")
    
    include_metadata: bpy.props.BoolProperty(
        name="Metadata",
        description="Store current 'Metadata' settings")

    include_post_processing: bpy.props.BoolProperty(
        name="Post Processing", # Currently not exposed to the user
        description="Store current 'Post Processing' settings",
        default=True)


class LOOM_PG_slots(bpy.types.PropertyGroup):
    # name: bpy.props.StringPropery()
    orig: bpy.props.StringProperty()
    repl: bpy.props.StringProperty()

class LOOM_PG_paths(bpy.types.PropertyGroup):
    # name: bpy.props.StringPropery()
    id: bpy.props.IntProperty()
    orig: bpy.props.StringProperty()
    repl: bpy.props.StringProperty()
    slts: bpy.props.CollectionProperty(name="Slot Collection", type=LOOM_PG_slots)


class LOOM_PG_scene_settings(bpy.types.PropertyGroup):

    frame_input: bpy.props.StringProperty(
        name="Frames to render",
        description="Specify a range or single frames to render")

    filter_input: bpy.props.BoolProperty(
        name="Filter individual elements",
        description="Isolate numbers after exclude chars (^, !)",
        default=False)

    command_line: bpy.props.BoolProperty(
        name="Render using Command Line",
        description="Send frames to Command Line (background process)",
        default=False)

    is_rendering: bpy.props.BoolProperty(
        name="Render Flag",
        description="Determine whether Loom is rendering",
        default=False)

    override_render_settings: bpy.props.BoolProperty(
        name="Override render settings",
        description="Force to render with specified settings",
        default=False)

    threads: bpy.props.IntProperty(
        name="CPU Threads",
        description="Number of CPU threads to use simultaneously while rendering",
        min=1)
    
    sequence_encode: bpy.props.StringProperty(
        name="Image Sequence",
        description="Image Sequence",
        maxlen=1024)

    movie_path: bpy.props.StringProperty(
        name="Movie",
        description="Movie File output path",
        maxlen=1024)

    sequence_rename: bpy.props.StringProperty(
        name="New Sequence Name",
        description="New Sequence Name",
        maxlen=1024)

    lost_frames: bpy.props.StringProperty(
        name="Missing Frames",
        description="Missing Frames",
        default="",
        options={'SKIP_SAVE'})

    render_collection: bpy.props.CollectionProperty(
        name="Render Collection",
        type=LOOM_PG_render)

    batch_scan_folder: bpy.props.StringProperty(
        name="Folder",
        description="Folder",
        maxlen=1024)

    batch_render_idx: bpy.props.IntProperty(
        name="Collection Index",
        description="Collection Index")
       
    batch_render_coll: bpy.props.CollectionProperty(
        name="Batch Render Collection",
        type=LOOM_PG_batch_render)

    output_render_version: bpy.props.IntProperty(
        name = "Render Version",
        description="Render Version",
        default=1, 
        min=1,
        update=render_version)

    output_sync_comp: bpy.props.BoolProperty(
        name="Sync Compositor",
        description="Sync version string with File Output nodes",
        default=True)

    comp_image_settings: bpy.props.BoolProperty(
        name="Display Image Settings",
        description="Display Image Settings of each File Output Node",
        default=False)

    project_directory: bpy.props.StringProperty(
        name="Project Directory",
        description="Stores the path to the Project Directory",
        maxlen=1024)

    path_collection: bpy.props.CollectionProperty(
        name="Globals Path Collection",
        type=LOOM_PG_paths)

    scene_selection: bpy.props.BoolProperty(
        name="Limit by Object Selection",
        description="Only add Keyframes assigned to the Object(s) in Selection",
        default=False)
    
    ignore_scene_range: bpy.props.BoolProperty(
        name="Ignore Scene Range",
        description="Do not take the Frame Range of the Scene into account",
        default=False)

    all_markers_flag: bpy.props.BoolProperty(
        name="All Markers",
        description="Add all Markers to the list",
        default=False)
    
    render_preset_flags: bpy.props.PointerProperty(
        type=LOOM_PG_preset_flags)

    custom_render_presets: bpy.props.EnumProperty(
        name="Render Preset",
        description="Select a Custom Preset",
        items=render_preset_callback,
        options={'SKIP_SAVE'})

# -------------------------------------------------------------------
#    UI Operators
# -------------------------------------------------------------------

class LOOM_OT_render_threads(bpy.types.Operator):
    """Set all available threads"""
    bl_idname = "loom.available_threads"
    bl_label = "Reset Threads"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from multiprocessing import cpu_count
        context.scene.loom.threads = cpu_count()
        self.report({'INFO'}, "Set to core maximum")
        return {'FINISHED'}


class LOOM_OT_render_full_scale(bpy.types.Operator):
    """Set Resolution Percentage Scale to 100%"""
    bl_idname = "loom.full_scale"
    bl_label = "Full Scale Image"
    bl_options = {'INTERNAL'}

    def execute(self, context): #context.area.tag_redraw()
        context.scene.render.resolution_percentage = 100
        return {'FINISHED'}


class LOOM_OT_guess_frames(bpy.types.Operator):
    """Either set the Range of the Timeline or find all missing Frames"""
    bl_idname = "loom.guess_frames"
    bl_label = "Set Timeline Range or detect missing Frames"
    bl_options = {'INTERNAL'}

    detect_missing_frames: bpy.props.BoolProperty(
            name="Missing Frames",
            description="Detect all missing Frames based based on the Output Path",
            default=True,
            options={'SKIP_SAVE'})

    def missing_frames(self, timeline_frames, rendered_frames):
        return sorted(set(timeline_frames).difference(rendered_frames))

    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def execute(self, context):
        glob_vars = context.preferences.addons[__name__].preferences.global_variable_coll
        scn = context.scene
        lum = scn.loom

        timeline_range = "{start}-{end}".format(start=scn.frame_start, end=scn.frame_end)
        timeline_inc = "{range}x{inc}".format(range=timeline_range, inc=scn.frame_step)
        lum.frame_input = timeline_inc if scn.frame_step != 1 else timeline_range
        
        """ Detect missing frames """
        if self.detect_missing_frames:
            image_sequence = {}
            given_filename = True

            fp = bpy.path.abspath(scn.render.filepath)
            output_folder, file_name = os.path.split(fp)
            output_folder = os.path.realpath(output_folder)

            if any(ext in file_name for ext in glob_vars.keys()):
                    file_name = replace_globals(file_name)
            if any(ext in output_folder for ext in glob_vars.keys()):
                output_folder = replace_globals(output_folder)

            if not file_name:
                given_filename = False
                blend_name, ext = os.path.splitext(os.path.basename(bpy.data.filepath))
                file_name = blend_name + "_"

            hashes = file_name.count('#')
            if not hashes:
                file_name = "{}{}".format(file_name, "#"*4)

            if file_name.endswith(tuple(scn.render.file_extension)):
                file_path = os.path.join(output_folder, file_name)
            else:
                file_path = os.path.join(output_folder, "{}{}".format(file_name, scn.render.file_extension))

            basedir, filename = os.path.split(file_path)
            basedir = os.path.realpath(bpy.path.abspath(basedir))
            filename_noext, extension = os.path.splitext(filename)
            hashes = filename_noext.count('#')
            name_real = filename_noext.replace("#", "")
            file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=extension)
            seq_name = "{}{}{}".format(name_real, hashes*"#", extension)

            if not os.path.exists(basedir):
                self.report({'INFO'}, 'Set to default range, "{}" does not exist on disk'.format(basedir))
                return {"CANCELLED"}

            for f in os.scandir(basedir):
                if f.name.endswith(extension) and f.is_file():
                    match = re.match(file_pattern, f.name, re.IGNORECASE)
                    if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

            if not len(image_sequence) > 1:
                if not given_filename:
                    return {"CANCELLED"}
                else:
                    # -> String needs to be split up, multiline "\" is not supported for INFO reports 
                    err_seq_name = 'No matching sequence with the name "{}" found in'.format(seq_name)
                    err_dir_name = 'directory "{}", set to default timeline range'.format(basedir)
                    self.report({'INFO'},"{} {}".format(err_seq_name, err_dir_name))
                return {"CANCELLED"}

            missing_frames = self.missing_frames(
                        [*range(scn.frame_start, scn.frame_end+1)],
                        sorted(list(image_sequence.keys())))

            if missing_frames:
                frames_to_render = self.rangify_frames(missing_frames)
                frame_count = len(missing_frames)
                lum.frame_input = frames_to_render
                self.report({'INFO'}, "{} missing Frame{} to render based on the output path: {} [{}]".format(
                    frame_count, 's'[:frame_count^1], seq_name, frames_to_render))
            else:
                self.report({'INFO'}, 'All given Frames are rendered, see "{}" folder'.format(basedir))
        return {'FINISHED'}

    def invoke(self, context, event):
        if event.ctrl or event.oskey:
            self.detect_missing_frames = False
        return self.execute(context)


class LOOM_OT_verify_frames(bpy.types.Operator):
    """Report all Frames to render & the current Render Location"""
    bl_idname = "loom.verify_frames"
    bl_label = "Verify Input Frames"
    bl_options = {'INTERNAL'}

    frame_input = None

    individual_frames: bpy.props.BoolProperty(
            name="Individual Frames",
            description="List all Frames individually",
            default=False,
            options={'SKIP_SAVE'})

    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def execute(self, context):
        scn = context.scene
        if self.frame_input:
            frame_count = len(self.frame_input)
            msg =  "{} Frame{} will be rendered".format(
                frame_count, 's'[:frame_count^1])
            if frame_count > 1:
                if not self.individual_frames:
                    msg += ": [{}]".format(self.rangify_frames(self.frame_input))
                else:
                    msg += ": [{}]".format(', '.join('{}'.format(i) for i in self.frame_input))
            self.report({'INFO'}, msg)
        else:
            self.report({'INFO'}, "No frames specified")
        return {'FINISHED'}

    def invoke(self, context, event):
        lum = context.scene.loom
        self.frame_input = filter_frames(
            lum.frame_input, context.scene.frame_step, lum.filter_input)
        if event.ctrl or event.oskey:
            self.individual_frames = True
        return self.execute(context)


class LOOM_OT_render_dialog(bpy.types.Operator):
    """Render Image Sequence Dialog"""
    bl_idname = "loom.render_dialog"
    bl_label = "Render Image Sequence"
    bl_options = {'REGISTER'}

    show_errors: bpy.props.BoolProperty(
        name="Show Errors",
        description="Displays Errors and Warnings",
        default=False,
        options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return not context.scene.render.is_movie_format

    def check(self, context):
        return True
    
    def write_permission(self, folder): # Hacky, but ok for now
        # https://stackoverflow.com/q/2113427/3091066
        try: # os.access(os.path.realpath(bpy.path.abspath(out_folder)), os.W_OK)
            pf = os.path.join(folder, "permission.txt")
            fh = open(pf, 'w')
            fh.close()
            os.remove(pf)
            return True
        except:
            return False

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        scn = context.scene
        lum = scn.loom
        filter_individual_numbers = lum.filter_input
        user_input = lum.frame_input

        """ Error handling """
        user_error = False

        if not self.options.is_invoke:
            user_error = True

        if not bpy.data.is_saved:
            self.report({'ERROR'}, "Blend-file not saved.")
            bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
            user_error = True
        
        if not scn.camera:
            self.report({'WARNING'}, "No camera in scene.")
            user_error = True
            if scn.use_nodes:
                user_error = False

        if not user_input and not any(char.isdigit() for char in user_input):
            self.report({'ERROR'}, "No frames to render.")
            user_error = True

        if user_error: #bpy.ops.loom.render_dialog('INVOKE_DEFAULT')
            return {"CANCELLED"}

        if not lum.override_render_settings:
            lum.property_unset("custom_render_presets")
            
        """ Start rendering headless or within the UI as usual """
        if lum.command_line:
            bpy.ops.loom.render_terminal(
                #debug=True,
                frames = user_input,
                threads = lum.threads,
                isolate_numbers = filter_individual_numbers,
                render_preset=lum.custom_render_presets)
        else:
            bpy.ops.render.image_sequence(
                frames = user_input, 
                isolate_numbers = filter_individual_numbers,
                render_silent = False)
        return {"FINISHED"}

    def invoke(self, context, event):
        scn = context.scene
        lum = scn.loom
        prefs = context.preferences.addons[__name__].preferences
        
        if not lum.is_property_set("frame_input") or not lum.frame_input:
            bpy.ops.loom.guess_frames(detect_missing_frames=False)
        #lum.property_unset("custom_render_presets") # Reset Preset Property

        if not prefs.is_property_set("terminal") or not prefs.terminal:
            bpy.ops.loom.verify_terminal()
        if not lum.is_property_set("threads") or not lum.threads:
            lum.threads = scn.render.threads  # *.5
        
        return context.window_manager.invoke_props_dialog(self, 
            width=(prefs.render_dialog_width))

    def draw(self, context):
        scn = context.scene
        lum = scn.loom
        prefs = context.preferences.addons[__name__].preferences
        layout = self.layout  #layout.label(text="Render Image Sequence")
        pref_view = context.preferences.view
        split_factor = .17

        split = layout.split(factor=split_factor)
        col = split.column(align=True)
        col.label(text="Frames:")
        col = split.column(align=True)
        sub = col.row(align=True) #GHOST_ENABLED
        # guess_icon = 'AUTO' if len(lum.render_collection) else 'PREVIEW_RANGE'
        sub.operator(LOOM_OT_guess_frames.bl_idname, icon='PREVIEW_RANGE', text="")
        sub.prop(lum, "frame_input", text="")
        sub.prop(lum, "filter_input", icon='FILTER', icon_only=True)
        #sub.prop(lum, "filter_keyframes", icon='SPACE2', icon_only=True)
        sub.operator(LOOM_OT_verify_frames.bl_idname, icon='GHOST_ENABLED', text="")

        split = layout.split(factor=split_factor)
        col = split.column(align=True)
        col.active = not lum.command_line
        col.label(text="Display:")
        col = split.column(align=True)
        sub = col.row(align=True)
        sub.active = not lum.command_line
        sub.prop(pref_view, "render_display_type", text="")
        sub.prop(scn.render, "use_lock_interface", icon_only=True)
            
        row = layout.row(align=True)    
        row.prop(lum, "command_line", text="Render using Command Line")
        if scn.render.resolution_percentage < 100:
            row.prop(self, "show_errors", text="", icon='TEXT' if self.show_errors else "REC", emboss=False)
        else:
            hlp = row.operator(LOOM_OT_openURL.bl_idname, icon='HELP', text="", emboss=False)
            hlp.description = "Open Loom Documentation on Github"
            hlp.url = "https://github.com/p2or/blender-loom"

        if lum.command_line:
            row = layout.row(align=True)
            row.prop(lum, "override_render_settings",  icon='PARTICLE_DATA', icon_only=True)
            if len(render_preset_callback(scn, context)) > 1:
                #split = row.split(factor=split_factor)
                #split.label(text="Preset:")
                #row = layout.row(align=True)
                preset = row.row(align=True)
                preset.prop(lum, "custom_render_presets", text="")
                preset.enabled = lum.override_render_settings
            else:
                thr_elem = row.row(align=True)
                thr_elem.active = bool(lum.command_line and lum.override_render_settings)
                thr_elem.prop(lum, "threads")
                thr_elem.operator(LOOM_OT_render_threads.bl_idname, icon='LOOP_BACK', text="")
            layout.separator(factor=0.1)

        if self.show_errors:
            res_percentage = scn.render.resolution_percentage
            if res_percentage < 100:
                row = layout.row()
                row.label(text="Warning: Resolution Percentage Scale is set to {}%".format(res_percentage))
                row.operator(LOOM_OT_render_full_scale.bl_idname, icon="INDIRECT_ONLY_OFF", text="", emboss=False)


class LOOM_OT_render_input_dialog(bpy.types.Operator):
    """Pass custom Frame Numbers and Ranges to the Render Dialog"""
    bl_idname = "loom.render_input_dialog"
    bl_label = "Render Frames"
    bl_options = {'INTERNAL'}

    frame_input: bpy.props.StringProperty()

    def execute(self, context):
        if self.frame_input:
            context.scene.loom.frame_input = self.frame_input
            bpy.ops.loom.render_dialog('INVOKE_DEFAULT')
            return {'FINISHED'}
        else:
            return {'CANCELLED'}
        

class LOOM_OT_selected_keys_dialog(bpy.types.Operator):
    """Render selected Keyframes in the Timeline, Graph Editor or Dopesheet"""
    bl_idname = "loom.render_selected_keys"
    bl_label = "Render Selected Keyframes"
    bl_options = {'REGISTER'}

    limit_to_object_selection: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    limit_to_scene_frames: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    all_keyframes: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    
    def int_filter(self, flt):
        try:
            return int(flt)
        except ValueError:
            return None

    def rangify_frames(self, frames):
        """ Converts a list of integers to range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def keyframes_from_actions(self, context, object_selection=False, keyframe_selection=True):
        """ Returns either selected keys by object selection or all keys """
        actions = bpy.data.actions
        if object_selection:
            obj_actions = [i.animation_data.action for i in context.selected_objects if i.animation_data]
            if obj_actions:
                actions = obj_actions
        # There is a select flag for the handles:
        # key.select_left_handle & key.select_right_handle
        ctrl_points = set()
        for action in actions:
            for channel in action.fcurves: #if channel.select:
                for key in channel.keyframe_points:
                    if keyframe_selection:
                        if key.select_control_point:
                            ctrl_points.add(key.co.x)
                    else:
                        ctrl_points.add(key.co.x)
        return sorted(ctrl_points)

    def keyframes_from_channel(self, action):
        """ Returns selected keys based on the action in the action editor """
        ctrl_points = set()
        for channel in action.fcurves:
            for key in channel.keyframe_points:
                if key.select_control_point:
                    ctrl_points.add(key.co.x)
        return sorted(ctrl_points)

    def selected_ctrl_points(self, context):
        """ Returns selected keys in the dopesheet if a channel is selected """
        ctrl_points = set()
        for sel_keyframe in context.selected_editable_keyframes:
            if sel_keyframe.select_control_point:
                    ctrl_points.add(sel_keyframe.co.x)
        return sorted(ctrl_points)

    def channel_ctrl_points(self):
        """ Returns all keys of selected channels in dopesheet """
        ctrl_points = set()
        for action in bpy.data.actions:
            for channel in action.fcurves:
                if channel.select: #print(action, channel.group)
                    for key in channel.keyframe_points:
                        ctrl_points.add(key.co.x)
        return sorted(ctrl_points)

    def selected_gpencil_frames(self, context):
        """ Returns all selected grease pencil frames """
        ctrl_points = set()
        for o in context.selected_objects:
            if o.type == 'GPENCIL':
                for l in o.data.layers:
                    for f in l.frames:
                        if f.select:
                            ctrl_points.add(f.frame_number)
        return sorted(ctrl_points)

    @classmethod
    def poll(cls, context):
        editors = ('DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'TIMELINE')
        '''
        areas = [a.type for a in context.screen.areas]
        return any((True for x in areas if x in editors))
        '''
        return context.space_data.type in editors and \
            not context.scene.render.is_movie_format
    
    def invoke(self, context, event):
        if event.ctrl:
            self.all_keyframes = True
        if event.alt:
            self.limit_to_scene_frames = True
        return self.execute(context)

    def execute(self, context):
        space = context.space_data

        selected_keys = None
        if space.type == 'DOPESHEET_EDITOR':
            mode = context.space_data.mode

            if mode == 'GPENCIL':
                selected_keys = self.selected_gpencil_frames(context)
            
            elif mode == 'ACTION':
                selected_keys = self.keyframes_from_channel(context, space.action)

            elif mode == 'MASK':
                self.report({'ERROR'}, "Not implemented.")
                return {"CANCELLED"}
            
            elif mode == 'CACHEFILE':
                self.report({'ERROR'}, "Not implemented.")
                return {"CANCELLED"}

            else: # Mode can be: DOPESHEET, 'SHAPEKEY'
                # if context.space_data.mode == 'DOPESHEET':
                if self.limit_to_object_selection and not context.selected_objects:
                    self.report({'ERROR'}, "No Object(s) selected")
                    return {"CANCELLED"}

                selected_keys = self.keyframes_from_actions(
                        context = context,
                        object_selection = self.limit_to_object_selection, 
                        keyframe_selection = not self.all_keyframes)

        elif space.type == 'GRAPH_EDITOR':
            selected_keys = self.keyframes_from_actions(
                    context = context,
                    object_selection = self.limit_to_object_selection, 
                    keyframe_selection = not self.all_keyframes)
        
        if not selected_keys:
            self.report({'ERROR'}, "No Keyframes selected")
            return {"CANCELLED"}

        """ Return integers whenever possible """
        int_frames = [self.int_filter(frame) for frame in selected_keys]
        frames = selected_keys if None in int_frames else int_frames

        if self.limit_to_scene_frames:
            scn = context.scene
            frames = set(frames).intersection(range(scn.frame_start, scn.frame_end+1))
            if not frames:
                self.report({'ERROR'}, "No frames keyframes in scene range")
                return {"CANCELLED"}

        bpy.ops.loom.render_input_dialog(frame_input=self.rangify_frames(frames))
        return {'FINISHED'}


class LOOM_OT_selected_makers_dialog(bpy.types.Operator):
    """Render selected Markers in the Timeline or Dopesheet"""
    bl_idname = "loom.render_selected_markers"
    bl_label = "Render Selected Markers"
    bl_options = {'REGISTER'}

    all_markers: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def rangify_frames(self, frames):
        """ Converts a list of integers to range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    @classmethod
    def poll(cls, context):
        editors = ('DOPESHEET_EDITOR', 'TIMELINE')
        return context.space_data.type in editors and \
            not context.scene.render.is_movie_format

    def invoke(self, context, event):
        if event.alt:
            self.all_markers = True
        return self.execute(context)

    def execute(self, context):
        if not self.all_markers:
            markers = sorted(m.frame for m in context.scene.timeline_markers if m.select)
        else:
            markers = sorted(m.frame for m in context.scene.timeline_markers)

        if not markers:
            if not self.all_markers:
                self.report({'ERROR'}, "Select any Marker to add or enable 'All Markers'.")
            else:
                self.report({'ERROR'}, "No Markers to add.")
            return {"CANCELLED"}

        bpy.ops.loom.render_input_dialog(frame_input=self.rangify_frames(markers))
        return {'FINISHED'}


def codec_callback(scene, context):
    codec = [
        ('PRORES422', "Apple ProRes 422", ""),
        ('PRORES422HQ', "Apple ProRes 422 HQ", ""),
        ('PRORES422LT', "Apple ProRes 422 LT", ""),
        ('PRORES422PR', "Apple ProRes 422 Proxy", ""),
        ('PRORES4444', "Apple ProRes 4444", ""),
        ('PRORES4444XQ', "Apple ProRes 4444 XQ", ""),
        ('DNXHD422-08-036', "Avid DNxHD 422 8-bit 36Mbit", ""),
        ('DNXHD422-08-145', "Avid DNxHD 422 8-bit 145Mbit", ""),
        ('DNXHD422-08-145', "Avid DNxHD 422 8-bit 220Mbit", ""),
        ('DNXHD422-10-185', "Avid DNxHD 422 10-bit 185Mbit", ""),
        #('DNXHD422-10-440', "Avid DNxHD 422 10-bit 440Mbit", ""),
        #('DNXHD444-10-350', "Avid DNxHD 422 10-bit 440Mbit", ""),
        ('DNXHR-444', "Avid DNxHR 444 10bit", ""),
        ('DNXHR-HQX', "Avid DNxHR HQX 10bit", ""),
        ('DNXHR-HQ', "Avid DNxHR HQ 8bit", ""),
        ('DNXHR-SQ', "Avid DNxHR SQ 8bit", "")
    ]
    return codec

def colorspace_callback(scene, context):
    colorspace = [
        ('iec61966_2_1', "sRGB", ""),
        ('bt709', "rec709", ""),
        ('gamma22', "Gamma 2.2", ""),
        ('gamma28', "Gamma 2.8", ""),
        ('linear', "Linear", "")
    ]
    return colorspace


class LOOM_MT_display_settings(bpy.types.Menu):
    bl_label = "Loom Batch Display Settings"
    bl_idname = "LOOM_MT_display_settings"

    def draw(self, context):
        prefs = context.preferences.addons[__name__].preferences
        layout = self.layout
        layout.label(text="Display Settings", icon="COLOR")
        layout.separator()
        layout.prop(prefs, "batch_paths_flag")
        layout.prop(prefs, "batch_dialog_rows")
        if prefs.batch_paths_flag:
            layout.prop(prefs, "batch_path_col_width")
        else:
            layout.prop(prefs, "batch_name_col_width")
        layout.operator("loom.batch_dialog_reset_display", icon="ANIM")


class LOOM_UL_batch_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        prefs = context.preferences.addons[__name__].preferences
        if prefs.batch_paths_flag:
            split = layout.split(factor=prefs.batch_path_col_width, align=True)
            split_left = split.split(factor=0.08)
            split_left.label(text="{:02d}".format(index+1))
            split_left.label(text=item.path, icon='FILE_BLEND')
        else:
            split = layout.split(factor=prefs.batch_name_col_width, align=True)
            split_left = split.split(factor=0.1)
            split_left.operator("loom.batch_default_frames", text="{:02d}".format(index+1), emboss=False).item_id = index
            split_left.label(text=item.name, icon='FILE_BLEND')
            
        split_right = split.split(factor=.99)
        row = split_right.row(align=True)
        row.operator("loom.batch_default_frames", icon="PREVIEW_RANGE", text="").item_id = index
        row.prop(item, "frames", text="") #, icon='IMAGEFILE'
        #row = split_right.row(align=True) #row.prop(item, "input_filter", text="", icon='FILTER')
        row.prop(item, "input_filter", text="", icon='FILTER')

        row.prop(item, "encode_flag", text="", icon='FILE_MOVIE')
        row.operator("loom.batch_verify_input", text="", icon='GHOST_ENABLED').item_id = index
        row.separator()
        row.operator(LOOM_OT_open_folder.bl_idname, 
                icon="DISK_DRIVE", text="").folder_path = os.path.dirname(item.path)

    def invoke(self, context, event):
        pass   


class LOOM_OT_batch_dialog(bpy.types.Operator):
    """Loom Batch Render Dialog"""
    bl_idname = "loom.batch_render_dialog"
    bl_label = "Loom Batch"
    bl_options = {'REGISTER'}
   
    colorspace: bpy.props.EnumProperty(
        name="Colorspace",
        description="colorspace",
        items=colorspace_callback)

    codec: bpy.props.EnumProperty(
        name="Codec",
        description="Codec",
        items=codec_callback)
    
    fps: bpy.props.IntProperty(
        name="Frame Rate",
        description="Frame Rate",
        default=25, min=1)

    terminal: bpy.props.BoolProperty(
        name="Terminal Instance",
        description="Render in new Terminal Instance",
        default=True)

    override_render_settings: bpy.props.BoolProperty(
        name="Override Render Settings",
        default=False)

    render_preset: bpy.props.StringProperty(
        name="Render Preset",
        description="Pass a custom Preset.py")

    shutdown: bpy.props.BoolProperty(
        name="Shutdown",
        description="Shutdown when done",
        default=False)

    def determine_type(self, val): #val = ast.literal_eval(s)
        if (isinstance(val, int)):
            return ("chi")
        elif (isinstance(val, float)):
            return ("chf")
        if val in ["true", "false"]:
            return ("chb")
        else:
            return ("chs")

    def pack_multiple_cmds(self, dct):
        rna_lst = []
        for key, args in dct.items():
            for i in args:
                rna_lst.append({"idc": key, "name": self.determine_type(i), "value": str(i)})
        return rna_lst

    def pack_arguments(self, lst):
        return [{"idc": 0, "name": self.determine_type(i), "value": str(i)} for i in lst]

    def write_permission(self, folder): # Hacky, but ok for now
        # https://stackoverflow.com/q/2113427/3091066
        try: # os.access(os.path.realpath(bpy.path.abspath(out_folder)), os.W_OK)
            pf = os.path.join(folder, "permission.txt")
            fh = open(pf, 'w')
            fh.close()
            os.remove(pf)
            return True
        except:
            return False

    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def verify_app(self, cmd):
        try:
            subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
        return True

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        lum = context.scene.loom
        black_list = []

        """ Error handling """
        user_error = False
        ffmpeg_error = False

        if not bool(lum.batch_render_coll):
            self.report({'ERROR'}, "No files to render.")
            user_error = True

        for item in lum.batch_render_coll:
            if not item.frames and not any(char.isdigit() for char in item.frames):
                self.report({'ERROR'}, "{} [wrong frame input]".format(item.name))
                user_error = True

            if not os.path.isfile(item.path):
                self.report({'ERROR'}, "{} does not exist anymore".format(item.name))
                user_error = True

            """ encode errors """
            if item.encode_flag:
                
                """ Verify ffmpeg """
                if not prefs.ffmpeg_path:
                    if self.verify_app(["ffmpeg", "-h"]):
                        prefs.ffmpeg_path = "ffmpeg"
                    else:
                        ffmpeg_error = True
                
                elif prefs.ffmpeg_path and prefs.ffmpeg_path != "ffmpeg":
                    if not os.path.isabs(prefs.ffmpeg_path) or prefs.ffmpeg_path.startswith('//'):
                        ffmpeg_bin = os.path.realpath(bpy.path.abspath(prefs.ffmpeg_path))
                        if os.path.isfile(ffmpeg_bin): 
                            prefs.ffmpeg_path = ffmpeg_bin            
                    if not self.verify_app([prefs.ffmpeg_path, "-h"]):
                        ffmpeg_error = True

                """ verify frames """
                frames_user = filter_frames(frame_input=item.frames, filter_individual=item.input_filter)
                if self.missing_frames(frames_user):
                    black_list.append(item.name)
                    info = "Encoding {} will be skipped [Missing Frames]".format(item.name)
                    self.report({'INFO'}, info)

            """
            out_folder, out_filename = os.path.split(bpy.path.abspath(context.scene.render.filepath))
            if not self.write_permission(os.path.realpath(out_folder)):
                self.report({'ERROR'}, "Specified output folder does not exist (permission denied)")
                user_error = True
            """

        if len(black_list) > 1:
            self.report({'ERROR'}, "Can not encode: {} (missing frames)".format(", ".join(black_list)))
            user_error = True

        if user_error or ffmpeg_error:
            if ffmpeg_error:
                self.report({'ERROR'}, "Path to ffmpeg binary not set in Addon preferences")
            bpy.ops.loom.batch_render_dialog('INVOKE_DEFAULT')
            return {"CANCELLED"}

        if not self.properties.is_property_set("render_preset"):
            self.render_preset = lum.custom_render_presets
        else:
            preset_path = os.path.join(prefs.render_presets_path, self.render_preset)
            if not os.path.exists(preset_path):
                self.report({'ERROR'}, "Given preset file does not exist {}".format(preset_path))
                bpy.ops.loom.batch_render_dialog('INVOKE_DEFAULT')
                return {"CANCELLED"}

        # Wrap blender binary path in quotations
        bl_bin = '"{}"'.format(bpy.app.binary_path) if not platform.startswith('win32') else bpy.app.binary_path

        cli_arg_dict = {}
        for c, item in enumerate(lum.batch_render_coll):
            python_expr = ("import bpy;" +\
                    "bpy.ops.render.image_sequence(" +\
                    "frames='{fns}', isolate_numbers={iel}," +\
                    "render_silent={cli}").format(
                        fns=item.frames,
                        iel=item.input_filter, 
                        cli=True)
            
            if self.override_render_settings and self.render_preset != 'EMPTY':
                python_expr += ", render_preset='{pst}'".format(pst=self.render_preset)
            
            python_expr += ");"
            python_expr += "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)"
            #print(type(python_expr), python_expr, self.render_preset)

            cli_args = [bl_bin, "-b", item.path, "--python-expr", python_expr]
            cli_arg_dict[c] = cli_args

        coll_len = len(cli_arg_dict)
        for c, item in enumerate(lum.batch_render_coll):
            if item.encode_flag and item.name not in black_list:
                # bpy.context.scene.loom.render_collection[-1]['file_path'];
                # seq_path=bpy.context.scene.render.frame_path(frame=1);
                python_expr = ("import bpy;" +\
                            "ext=bpy.context.scene.render.file_extension;" +\
                            "seq_path=bpy.context.scene.render.filepath+ext;" +\
                            "bpy.ops.loom.encode_dialog(" +\
                            "sequence=seq_path," +\
                            "fps={fps}," +\
                            "codec='{cdc}'," +\
                            "colorspace='{cls}'," +\
                            "terminal_instance=False," +\
                            "pause=False)").format(
                                fps = self.fps,
                                cdc = self.codec,
                                cls = self.colorspace)

                cli_args = [bl_bin, "-b", item.path, "--python-expr", python_expr]
                cli_arg_dict[c+coll_len] = cli_args

        """ Start headless batch """
        bpy.ops.loom.run_terminal(
            #debug_arguments=True,
            binary="",
            terminal_instance=self.terminal,
            argument_collection=self.pack_multiple_cmds(cli_arg_dict),
            bash_name="loom-batch-temp",
            force_bash=True,
            shutdown=self.shutdown)

        return {'FINISHED'}

    def invoke(self, context, event):
        prefs = context.preferences.addons[__name__].preferences
        context.scene.loom.property_unset("custom_render_presets")
        return context.window_manager.invoke_props_dialog(self, 
            width=(prefs.batch_dialog_width))

    def check(self, context):
        return True

    def draw(self, context):
        prefs = context.preferences.addons[__name__].preferences
        scn = context.scene
        lum = scn.loom
        
        layout = self.layout
        row = layout.row()

        row.template_list(
            listtype_name = "LOOM_UL_batch_list", 
            list_id = "", 
            dataptr = lum,
            propname = "batch_render_coll", 
            active_dataptr = lum, 
            active_propname = "batch_render_idx", 
            rows=prefs.batch_dialog_rows)
        
        col = row.column(align=True)
        col.operator(LOOM_OT_batch_selected_blends.bl_idname, icon='ADD', text="")
        col.operator(LOOM_OT_batch_list_actions.bl_idname, icon='REMOVE', text="").action = 'REMOVE'
        col.menu(LOOM_MT_display_settings.bl_idname, icon='DOWNARROW_HLT', text="")
        col.separator()
        col.separator()
        col.operator(LOOM_OT_batch_list_actions.bl_idname, icon='TRIA_UP', text="").action = 'UP'
        col.operator(LOOM_OT_batch_list_actions.bl_idname, icon='TRIA_DOWN', text="").action = 'DOWN'

        layout.row() # Seperator
        row = layout.row(align=True)
        col = row.column(align=True)
        col.operator(LOOM_OT_batch_selected_blends.bl_idname, icon="DOCUMENTS")
        row = col.row(align=True)
        row.operator(LOOM_OT_scan_blends.bl_idname, icon='ZOOM_SELECTED') #VIEWZOOM
        if bpy.data.is_saved: # icon="WORKSPACE"
            row.operator(LOOM_OT_batch_snapshot.bl_idname, icon="IMAGE_BACKGROUND", text="Add Snapshot")
        
        layout.row() # Seperator
        row = layout.row(align=True)
        sub_row = row.row(align=True)
        sub_row.operator(LOOM_OT_batch_remove_doubles.bl_idname, text="Remove Duplicates", icon="SEQ_SPLITVIEW")
        sub_row.operator(LOOM_OT_batch_clear_list.bl_idname, text="Clear List", icon="PANEL_CLOSE")
        
        if any(i.encode_flag for i in lum.batch_render_coll):
            row = layout.row()
            row.separator()
            split_perc = 0.3
            row = layout.row()
            split = row.split(factor=split_perc)
            split.label(text="Colorspace")
            split.prop(self, "colorspace", text="")
            row = layout.row()
            split = row.split(factor=split_perc)
            split.label(text="Frame Rate")
            split.prop(self, "fps", text="")
            row = layout.row()
            split = row.split(factor=split_perc)
            split.label(text="Codec")
            split.prop(self, "codec", text="")
            row = layout.row()
            row.separator()

        layout.separator(factor=0.5)
        row = layout.row() #if platform.startswith('win32'):
        row.prop(self, "shutdown", text="Shutdown when done")
        if len(render_preset_callback(scn, context)) > 1:
            settings_icon = 'MODIFIER_ON' if self.override_render_settings else 'MODIFIER_OFF'
            row.prop(self, "override_render_settings", icon=settings_icon, text="", emboss=False)
            if self.override_render_settings:
                layout.separator()
                layout.prop(lum, "custom_render_presets", text="Render Settings Override")
                layout.separator()
        row = layout.row()


class LOOM_OT_batch_snapshot(bpy.types.Operator):
    """Create a Snapshot from the current Blend File"""
    bl_idname = "loom.batch_snapshot"
    bl_label = "Snapshot"
    bl_options = {'INTERNAL'}
    bl_property = "file_name"
    
    snapshot_folder: bpy.props.StringProperty(
        name="Snapshot Folder",
        description="Folder to copy the snapshot to",
        default="//tmp",
        subtype='DIR_PATH')
        
    file_name: bpy.props.StringProperty(
        name="Filename",
        description="The filename used for the copy",
        default="",
        options={'SKIP_SAVE'})
    
    suffix: bpy.props.EnumProperty(
        name="Filename",
        description="Apply or Restore Paths",
        default = 'DATE',
        items=(
            ('DATE', "Date (no Suffix)", ""),
            ('NUMBSUFF', "Number Suffix", ""),
            ('DATESUFF', "Date Suffix", "")))

    overwrite: bpy.props.BoolProperty(
        name="Overwrite File",
        description="Overwrite existing files",
        default=False)
    
    apply_globals: bpy.props.BoolProperty(
        name="Apply Globals",
        default=False)

    globals_flag: bpy.props.BoolProperty(
        name="Globals Flag",
        options={'HIDDEN'},
        default=False)

    def number_suffix(self, filename_no_extension):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename_no_extension)])
        return next(reversed(digits), None)

    def file_sequence(self, filepath, digits=None, extension=None):
        file_sequence = {}
        basedir, filename = os.path.split(filepath)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)
        num_suffix = self.number_suffix(filename_noext)
        filename = filename_noext.replace(num_suffix,'') if num_suffix else filename_noext
        if extension: ext = extension
        if digits:
            file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=filename, ds=digits, ex=ext)
        else:
            file_pattern = r"{fn}(\d+)\.?{ex}".format(fn=filename, ex=ext)
        
        for f in os.scandir(basedir):
            if f.name.endswith(ext) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: file_sequence[int(match.group(1))] = os.path.join(basedir, f.name)
        return file_sequence

    @classmethod
    def poll(cls, context):
        return bpy.data.is_saved

    def execute(self, context):
        snap_dir = self.snapshot_folder
        if not self.properties.is_property_set("snapshot_folder"):
            snap_dir = context.preferences.addons[__name__].preferences.snapshot_directory
        
        basedir = os.path.realpath(bpy.path.abspath(snap_dir))
        fn_noext, ext = os.path.splitext(self.file_name)
        fn_noext = fn_noext if self.file_name else "00"
        ext = ext if ext else ".blend"
        
        ''' Create the folder if not present '''
        if not os.path.exists(basedir):
            bpy.ops.loom.create_directory(directory=basedir)
        
        ''' Format the filename '''
        fcopy = None
        if self.suffix == 'NUMBSUFF':
            leading_zeros = 2 # Expose?
            suff = "{:0{}d}".format(1, leading_zeros)
            bound_filename = suff if not self.file_name else "{}_{}".format(fn_noext, suff)
            fcopy = os.path.join(basedir, "{}{}".format(bound_filename, ext))

            if os.path.isfile(fcopy) and not self.overwrite:
                fs = self.file_sequence(fcopy, digits=leading_zeros)
                if fs:
                    suff = "{:0{}d}".format(max(fs.keys())+1, leading_zeros)
                    nextf = suff if not self.file_name else "{}_{}".format(fn_noext, suff)
                    #last_number, last_path = list(fs.items())[-1] # Python 3.8+
                    fcopy = os.path.join(basedir, "{}{}".format(nextf, ext))
        else:
            ft = strftime("%Y-%m-%d-%H-%M-%S")
            if self.suffix == 'DATE' and self.options.is_invoke:
                fcopy = os.path.join(basedir, "{}{}".format(ft, ext))
            else:
                date_fn = ft if not self.file_name else "{}_{}".format(fn_noext, ft)       
                fcopy = os.path.join(basedir, "{}{}".format(date_fn, ext))
        
        ''' Save a copy and add it to Loom Batch '''
        if os.path.exists(basedir) and fcopy is not None:
            
            if self.apply_globals: bpy.ops.loom.globals_bake(action='APPLY')
            bpy.ops.wm.save_as_mainfile(filepath=fcopy, copy=True)
            if self.apply_globals: bpy.ops.loom.globals_bake(action='RESET')
            
            if not os.path.isfile(fcopy):
                self.report({'WARNING'},"Can not save a copy of the current file")
                return {"CANCELLED"}
            else:
                self.report({'INFO'},"Snapshot created: {}".format(fcopy))

            ''' Add the snapshot to the list '''
            if self.options.is_invoke:
                fd, fn = os.path.split(fcopy)
                scn = context.scene
                lum = scn.loom

                data = blend_render_info.read_blend_rend_chunk(fcopy)
                if not data:
                    self.report({'WARNING'}, "Skipped {}, invalid .blend file".format(fcopy))
                    return {'CANCELLED'}
                else:
                    start, end, sc = data[0]
                    item = lum.batch_render_coll.add()
                    item.rid = len(lum.batch_render_coll)
                    item.name = fn
                    item.path = fcopy
                    item.frame_start = start
                    item.frame_end = end
                    item.scene = sc
                    item.frames = "{}-{}".format(item.frame_start, item.frame_end)
                    lum.batch_render_idx = len(lum.batch_render_coll)-1

        return {'FINISHED'}

    def invoke(self, context, event):
        if user_globals(context):
            self.globals_flag = True
        if bpy.data.filepath:
            self.file_name = bpy.path.basename(bpy.data.filepath)[:-6]
        return context.window_manager.invoke_props_dialog(self, width=450)
        #else: return self.execute(context)

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row = row.prop(self, "suffix", expand=True)
        if self.suffix != 'DATE':
            row = layout.row(align=True)
            row.prop(self, "file_name")
        if self.globals_flag:
            row = layout.row(align=True)
            row.prop(self, "apply_globals")
        '''
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(self, "suffix", expand=True)
        if self.globals_flag:
            row = col.row(align=True)
            row.prop(self, "apply_globals", toggle=True)
        '''
        layout.row()


class LOOM_OT_batch_selected_blends(bpy.types.Operator, ImportHelper):
    """Select Blend Files via File Browser"""
    bl_idname = "loom.batch_select_blends"
    bl_label = "Select Blend Files"
    bl_options = {'INTERNAL'}

    filename_ext = ".blend"
    filter_glob: bpy.props.StringProperty(
            default="*.blend",
            options={'HIDDEN'},
            maxlen=255)

    files: bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)        
    cursor_pos = [0,0]
    
    def display_popup(self, context):
        win = context.window #win.cursor_warp((win.width*.5)-100, (win.height*.5)+100)
        win.cursor_warp(x=self.cursor_pos[0], y=self.cursor_pos[1]+100) # re-invoke the dialog
        bpy.ops.loom.batch_render_dialog('INVOKE_DEFAULT')

    def cancel(self, context):
        self.display_popup(context)

    def invoke(self, context, event):
        self.cursor_pos = [event.mouse_x, event.mouse_y]
        self.filename = ""
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    
    def execute(self, context):
        scn = context.scene
        lum = scn.loom
        
        valid_files, invalid_files = [], []
        start, end, sc = [1, 250, "Scene"]
        for i in self.files:
            path_to_file = os.path.join(os.path.dirname(self.filepath), i.name)
            if os.path.isfile(path_to_file):
                
                # /Blender <version>/<version>/scripts/modules/blender_render_info.py
                # https://blender.stackexchange.com/a/55503/3710
                data = blend_render_info.read_blend_rend_chunk(path_to_file)
                if not data:
                    invalid_files.append(i.name)
                    self.report({'INFO'}, "Can not read frame range from {}, invalid .blend".format(i.name))
                else:
                    valid_files.append(i.name)
                    start, end, sc = data[0]

                item = lum.batch_render_coll.add()
                item.rid = len(lum.batch_render_coll)
                item.name = i.name
                item.path = path_to_file
                item.frame_start = start
                item.frame_end = end
                item.scene = sc
                item.frames = "{}-{}".format(item.frame_start, item.frame_end)
        
        #self.report({'INFO'}, "Skipped {}, no valid .blend".format(", ".join(valid_files)))
        if invalid_files:
            self.report({'WARNING'}, "Can not read frame range from {}, invalid .blend file(s)".format(", ".join(invalid_files)))
        elif valid_files:
            self.report({'INFO'}, "Added {} to the list".format(", ".join(valid_files)))
        else:
            self.report({'INFO'}, "Nothing selected")
 
        lum.batch_render_idx = len(lum.batch_render_coll)-1
        self.display_popup(context)
        return {'FINISHED'}
    

class LOOM_OT_scan_blends(bpy.types.Operator, ImportHelper):
    """Scan directory for blend files and add to list"""
    bl_idname = "loom.batch_scandir_blends"
    bl_label = "Scan Directory for Blend Files"
    bl_options = {'INTERNAL'}

    # ImportHelper mixin class uses this
    filename_ext = ".blend"

    filter_glob: bpy.props.StringProperty(
            default="*.blend",
            options={'HIDDEN'},
            maxlen=255)

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    sub_folders: bpy.props.BoolProperty(default=True, name="Scan Subfolders")
    cursor_pos = [0,0]

    def blend_files(self, base_dir, recursive):
        # Limitation: https://bugs.python.org/issue26111
        # https://stackoverflow.com/q/14710708/3091066
        for entry in os.scandir(base_dir):
            try:
                if entry.is_file() and entry.name.endswith(".blend"):
                    yield entry
                elif entry.is_dir() and recursive:
                    yield from self.blend_files(entry.path, recursive)
            except WindowsError:
                self.report({'WARNING'},"Access denied: {} (not a real directory)".format(entry.name))

    def display_popup(self, context):
        win = context.window #win.cursor_warp((win.width*.5)-100, (win.height*.5)+100)
        win.cursor_warp(x=self.cursor_pos[0], y=self.cursor_pos[1]+100) # re-invoke the dialog
        bpy.ops.loom.batch_render_dialog('INVOKE_DEFAULT')
        #bpy.context.window.screen = bpy.context.window.screen
        
    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        scn = context.scene
        lum = scn.loom
        lum.batch_scan_folder = self.directory
        
        if not self.directory:
            return {'CANCELLED'}

        blend_files = self.blend_files(self.directory, self.sub_folders)
        if next(blend_files, None) is None:
            self.display_popup(context)
            self.report({'WARNING'},"No blend files found in {}".format(self.directory))
            return {'CANCELLED'}
        
        valid_files, invalid_files = [], []
        for i in blend_files:
            path_to_file = (i.path)
            data = blend_render_info.read_blend_rend_chunk(path_to_file)
            if not data:
                invalid_files.append(i.name)
            else:
                valid_files.append(i.name)
                start, end, sc = data[0]
                start, end, sc = data[0]
                item = lum.batch_render_coll.add()
                item.rid = len(lum.batch_render_coll)
                item.name = i.name
                item.path = path_to_file
                item.frame_start = start
                item.frame_end = end
                item.scene = sc
                item.frames = "{}-{}".format(item.frame_start, item.frame_end)

        if valid_files:
             self.report({'INFO'}, "Added {} to the list".format(", ".join(valid_files)))
        if invalid_files:
            self.report({'WARNING'}, "Skipped {}, invalid .blend file(s)".format(", ".join(invalid_files)))

        lum.batch_render_idx = len(lum.batch_render_coll)-1
        self.display_popup(context)
        return {'FINISHED'}

    def cancel(self, context):
        self.display_popup(context)

    def invoke(self, context, event):
        self.cursor_pos = [event.mouse_x, event.mouse_y]
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class LOOM_OT_batch_list_actions(bpy.types.Operator):
    """Loom Batch Dialog Actions"""
    bl_idname = "loom.batch_dialog_action"
    bl_label = "Loom Batch Dialog Action"
    bl_options = {'INTERNAL'}
    
    action: bpy.props.EnumProperty(
        items=(
            ('UP', "Up", ""),
            ('DOWN', "Down", ""),
            ('REMOVE', "Remove", ""),
            ('ADD', "Add", "")))

    def invoke(self, context, event):
        scn = context.scene
        lum = scn.loom
        idx = lum.batch_render_idx
        try:
            item = lum.batch_render_coll[idx]
        except IndexError:
            pass
        else:
            if self.action == 'DOWN' and idx < len(lum.batch_render_coll) - 1:
                item_next = lum.batch_render_coll[idx+1].name
                lum.batch_render_coll.move(idx, idx + 1)
                lum.batch_render_idx += 1

            elif self.action == 'UP' and idx >= 1:
                item_prev = lum.batch_render_coll[idx-1].name
                lum.batch_render_coll.move(idx, idx-1)
                lum.batch_render_idx -= 1

            elif self.action == 'REMOVE':
                info = '"{}" removed from list'.format(lum.batch_render_coll[lum.batch_render_idx].name)
                lum.batch_render_idx -= 1
                if lum.batch_render_idx < 0: lum.batch_render_idx = 0
                self.report({'INFO'}, info)
                lum.batch_render_coll.remove(idx)

        if self.action == 'ADD':
            bpy.ops.loom.batch_select_blends('INVOKE_DEFAULT')       
            lum.batch_render_idx = len(lum.batch_render_coll)

        return {"FINISHED"}


class LOOM_OT_batch_clear_list(bpy.types.Operator):
    """Clear all items of the Render Collection"""
    bl_idname = "loom.batch_clear_list"
    bl_label = "Delete all items of the list?"
    bl_options = {'INTERNAL'}
    
    @classmethod
    def poll(cls, context):
        return bool(context.scene.loom.batch_render_coll)
    
    def execute(self, context):
        context.scene.loom.batch_render_coll.clear()
        self.report({'INFO'}, "All items removed")
        return {"FINISHED"}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)
    

class LOOM_OT_batch_dialog_reset(bpy.types.Operator):
    """Reset Batch Dialog Display Settings"""
    bl_idname = "loom.batch_dialog_reset_display"
    bl_label = "Reset Display Settings"
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        prefs.property_unset("batch_dialog_rows")
        prefs.property_unset("batch_paths_flag")
        prefs.property_unset("batch_path_col_width")
        prefs.property_unset("batch_name_col_width")       
        return {'FINISHED'}


class LOOM_OT_batch_remove_doubles(bpy.types.Operator):
    """Remove Duplicates in List based on the filename"""
    bl_idname = "loom.batch_remove_doubles"
    bl_label = "Remove All Duplicates?"
    bl_options = {'INTERNAL'}
    
    doubles = []

    def find_duplicates(self, context):
        path_lookup = {}
        for c, i in enumerate(context.scene.loom.batch_render_coll):
            path_lookup.setdefault(i.path, []).append(i.name)

        for path, names in path_lookup.items():
            for i in names[1:]:
                self.doubles.append(i)
        return len(self.doubles)

    @classmethod
    def poll(cls, context):
        return bool(context.scene.loom.batch_render_coll)
    
    def execute(self, context):
        lum = context.scene.loom
        removed_items = []
        for i in self.doubles:
            item_id = lum.batch_render_coll.find(i)
            lum.batch_render_coll.remove(item_id)
            removed_items.append(i)

        lum.batch_render_idx = (len(lum.batch_render_coll)-1)
        self.report({'INFO'}, "{} {} removed: {}".format(
                    len(removed_items),
                    "items" if len(removed_items) > 1 else "item",
                    ', '.join(set(removed_items))))
        return {'FINISHED'}

    def invoke(self, context, event):
        self.doubles.clear()
        if self.find_duplicates(context):
            return context.window_manager.invoke_confirm(self, event)
        else:
            self.report({'INFO'}, "No doubles in list, nothing to do.")
            return {'FINISHED'}


class LOOM_OT_batch_active_item(bpy.types.Operator):
    """Print active Item"""
    bl_idname = "loom.batch_active_item"
    bl_label = "Print Active Item to Console"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        lum = context.scene.loom
        try:
            print (lum.batch_render_coll[lum.batch_render_idx].name)
        except IndexError:
            print ("No active item")
        return{'FINISHED'}
    

class LOOM_OT_batch_default_range(bpy.types.Operator):
    """Revert to default frame range"""
    bl_idname = "loom.batch_default_frames"
    bl_label = "Revert to default frame range"
    bl_options = {'INTERNAL'}
    
    item_id: bpy.props.IntProperty()

    def execute(self, context):
        try:
            item = context.scene.loom.batch_render_coll[self.item_id]
            default_range = "{}-{}".format(item.frame_start, item.frame_end)
            item.frames = default_range
        except IndexError:
            self.report({'INFO'}, "No active item")
        return{'FINISHED'}


class LOOM_OT_batch_verify_input(bpy.types.Operator):
    """Verify Input Frame Range"""
    bl_idname = "loom.batch_verify_input"
    bl_label = "Verify Input Frame Range"
    bl_options = {'INTERNAL'}
    
    item_id: bpy.props.IntProperty()
    
    def execute(self, context):
        try:
            item = context.scene.loom.batch_render_coll[self.item_id]
        except IndexError:
            self.report({'INFO'}, "No active item") # redundant?
            return{'CANCELLED'}
        
        folder = os.path.realpath(bpy.path.abspath(item.path))
        frame_input = filter_frames(
            frame_input = item.frames, 
            filter_individual = item.input_filter)
        
        if frame_input:
            self.report({'INFO'}, ("{} {} [{}] will be rendered to {}".format(
                len(frame_input),
                "Frame" if len(frame_input) == 1 else "Frames",
                ', '.join('{}'.format(i) for i in frame_input), 
                folder)))
        else:
            self.report({'INFO'}, "No frames specified")
        return {'FINISHED'}


class LOOM_OT_encode_dialog(bpy.types.Operator):
    """Encode Image Sequence to ProRes or DNxHD"""
    bl_idname = "loom.encode_dialog"
    bl_label = "Encode Image Sequence"
    bl_options = {'REGISTER'}

    sequence: bpy.props.StringProperty(
        name="Path to sequence",
        description="Path to sequence",
        maxlen=1024,
        subtype='FILE_PATH')
    
    movie: bpy.props.StringProperty(
        name="Path to movie",
        description="Path to movie",
        maxlen=1024,
        subtype='FILE_PATH')

    fps: bpy.props.IntProperty(
        name="Frame Rate",
        description="Frame Rate",
        default=25, min=1)

    missing_frames_bool: bpy.props.BoolProperty(
        name="Missing Frames",
        description="Missing Frames")

    codec: bpy.props.EnumProperty(
        name="Codec",
        description="Codec",
        items=codec_callback)

    colorspace: bpy.props.EnumProperty(
        name="Colorspace",
        description="colorspace",
        items=colorspace_callback)
    
    terminal_instance: bpy.props.BoolProperty(
        name="New Terminal Instance",
        description="Opens Blender in a new Terminal Window",
        default=True)

    pause: bpy.props.BoolProperty(
        name="Confirm when done",
        description="Confirm when done",
        default=True)

    # https://avpres.net/FFmpeg/sq_ProRes.html, https://trac.ffmpeg.org/wiki/Encode/VFX
    encode_presets = {
        "PRORES422PR" : ["-c:v", "prores_ks", "-profile:v", 0],
        "PRORES422LT" : ["-c:v", "prores_ks", "-profile:v", 1],
        #"PRORES422" : ["-c:v", "prores", "-profile:v", 2, "-pix_fmt" "yuv422p10"], #["-c:v", "prores", "-profile:v", 2],
        "PRORES422" : ["-c:v", "prores_ks", "-profile:v", 2],
        "PRORES422HQ" : ["-c:v", "prores_ks", "-profile:v", 3],
        "PRORES4444" : ["-c:v", "prores_ks", "-profile:v", 4, "-quant_mat", "hq", "-pix_fmt", "yuva444p10le"],
        "PRORES4444XQ" : ["-c:v", "prores_ks", "-profile:v", 5, "-quant_mat", "hq", "-pix_fmt", "yuva444p10le"],
        "DNXHD422-08-036" : ["-c:v", "dnxhd", "-vf", "scale=1920x1080,fps=25/1,format=yuv422p", "-b:v", "36M"],
        "DNXHD422-08-145" : ["-c:v", "dnxhd", "-vf", "scale=1920x1080,fps=25/1,format=yuv422p", "-b:v", "145M"],
        "DNXHD422-08-145" : ["-c:v", "dnxhd", "-vf", "scale=1920x1080,fps=25/1,format=yuv422p", "-b:v", "220M"],
        "DNXHD422-10-185" : ["-c:v", "dnxhd", "-vf", "scale=1920x1080,fps=25/1,format=yuv422p10", "-b:v", "185M"],
        #"DNXHD422-10-440" : ["-c:v", "dnxhd", "-vf", "scale=1920x1080,fps=25/1,format=yuv422p10", "-b:v", "440M"],
        #"DNXHD444-10-350" : ["-c:v", "dnxhd", "-profile:v", "dnxhr_444", "-vf", "format=yuv444p10" "-b:v", "350M"],
        "DNXHR-444" : ["-c:v", "dnxhd", "-profile:v", "dnxhr_444", "-vf", "format=yuv444p10"],
        "DNXHR-HQX" : ["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx", "-vf", "format=yuv422p10"],
        "DNXHR-HQ" : ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-vf", "format=yuv422p"],
        "DNXHR-SQ" : ["-c:v", "dnxhd", "-profile:v", "dnxhr_sq", "-vf", "format=yuv422p"],
        }
    
    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def verify_app(self, cmd):
        try:
            subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:

            if e.errno == errno.ENOENT:
                return False
        return True

    def determine_type(self, val): 
        #val = ast.literal_eval(s)
        if (isinstance(val, int)):
            return ("chi")
        elif (isinstance(val, float)):
            return ("chf")
        if val in ["true", "false"]:
            return ("chb")
        else:
            return ("chs")

    def number_suffix(self, filename):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)

    def pack_arguments(self, lst):
        return [{"idc": 0, "name": self.determine_type(i), "value": str(i)} for i in lst]

    def check(self, context):
        return True        

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        prefs.default_codec = self.codec
        lum = context.scene.loom
        image_sequence = {}
        
        """ Verify ffmpeg """
        ffmpeg_error = False
        if not prefs.ffmpeg_path:
            if self.verify_app(["ffmpeg", "-h"]):
                prefs.ffmpeg_path = "ffmpeg"
            else:
                ffmpeg_error = True
        
        elif prefs.ffmpeg_path and prefs.ffmpeg_path != "ffmpeg":
            if not os.path.isabs(prefs.ffmpeg_path) or prefs.ffmpeg_path.startswith('//'):
                ffmpeg_bin = os.path.realpath(bpy.path.abspath(prefs.ffmpeg_path))
                if os.path.isfile(ffmpeg_bin): 
                    prefs.ffmpeg_path = ffmpeg_bin            
            if not self.verify_app([prefs.ffmpeg_path, "-h"]):
                ffmpeg_error = True
        
        if ffmpeg_error:
            error_message = "Path to ffmpeg binary not set in addon preferences"
            if not self.options.is_invoke:
                print (error_message)
                return {"CANCELLED"}
            else:
                self.report({'ERROR'},error_message)
                bpy.ops.loom.encode_dialog('INVOKE_DEFAULT')
                return {"CANCELLED"}
            
        #if not self.properties.is_property_set("sequence"):
        seq_path = lum.sequence_encode if not self.sequence else self.sequence
        mov_path = lum.movie_path if not self.movie else self.movie

        """ Operator called via UI """
        path_error = False
        if not seq_path:
            self.report({'ERROR'}, "No image sequence specified")
            path_error = True

        if path_error and self.options.is_invoke:
            bpy.ops.loom.encode_dialog('INVOKE_DEFAULT')
            return {"CANCELLED"}

        basedir, filename = os.path.split(seq_path)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, extension = os.path.splitext(filename)

        if not os.path.isdir(basedir):
            self.report({'ERROR'},"The main directory '{}' does not exist".format(basedir))
            return {"CANCELLED"}

        """ Support for non-sequence paths when called via Command Line """
        if not self.options.is_invoke:
            filename_noext = replace_globals(filename_noext)
            if '#' not in filename_noext:
                filename_noext += "####"
            if not extension:
                extension += context.scene.render.file_extension

        """ Verify image sequence """
        seq_error = False
        if '#' not in filename_noext:
            num_suff = self.number_suffix(filename_noext)
            if not num_suff:
                self.report({'ERROR'}, "No valid image sequence")
                seq_error = True
            else:
                filename_noext = filename_noext.replace(num_suff, "#"*len(num_suff))
        
        if not extension: # Sequence file format
            self.report({'ERROR'}, "File format not set (missing extension)")
            seq_error = True

        if seq_error and self.options.is_invoke:
            bpy.ops.loom.encode_dialog('INVOKE_DEFAULT')
            return {"CANCELLED"}

        hashes = filename_noext.count('#')
        name_real = filename_noext.replace("#", "")
        file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=extension)

        for f in os.scandir(basedir):
            if f.name.endswith(extension) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

        if not len(image_sequence) > 1:
            self.report({'ERROR'},"'{}' cannot be found on disk".format(filename))
            if self.options.is_invoke:
                bpy.ops.loom.encode_dialog('INVOKE_DEFAULT')
            return {"CANCELLED"}

        if not mov_path:
            mov_path = next(iter(image_sequence.values()))

        """ Verify movie file name and extension """
        mov_basedir, mov_filename = os.path.split(mov_path)
        mov_filename_noext, mov_extension = os.path.splitext(mov_filename)
        mov_extension = ".mov"

        """ In case the sequence has no name """
        if mov_filename_noext.isdigit():
            mov_filename_noext = os.path.basename(basedir)
        
        """ If a file with the same name already exists, do not overwrite it """
        mov_path = os.path.join(mov_basedir, "{}{}".format(mov_filename_noext, mov_extension))
        if os.path.isfile(mov_path):
            time_stamp = strftime("%Y-%m-%d-%H-%M-%S")
            mov_filename_noext = "{}_{}".format(mov_filename_noext, time_stamp)
        
        mov_path = os.path.join(mov_basedir, "{}{}".format(mov_filename_noext, mov_extension))

        """ Detect missing frames """
        frame_numbers = sorted(list(image_sequence.keys())) #start_frame, end_frame = fn[0], fn[-1]
        missing_frame_list = self.missing_frames(frame_numbers)

        if missing_frame_list:
            lum.lost_frames = self.rangify_frames(missing_frame_list)
            error = "Missing frames detected: {}".format(lum.lost_frames)
            if not self.options.is_invoke:
                print ("ERROR: ", error)
                return {"CANCELLED"}
            else:
                self.report({'ERROR_INVALID_INPUT'}, error)
                self.report({'ERROR'},"Frame list copied to clipboard.")
                context.window_manager.clipboard = "{}".format(
                    ','.join(map(str, missing_frame_list)))
                bpy.ops.loom.encode_dialog('INVOKE_DEFAULT') # re-invoke the dialog
                return {"CANCELLED"}
        else:
            lum.lost_frames = ""
            
        """ Format image sequence for ffmpeg """
        fn_ffmpeg = filename_noext.replace("#"*hashes, "%0{}d{}".format(hashes, extension))
        fp_ffmpeg = os.path.join(basedir, fn_ffmpeg) # "{}%0{}d{}".format(filename_noext, 4, ext)
        cli_args = ["-start_number", frame_numbers[0], "-apply_trc", self.colorspace, "-i", fp_ffmpeg] 
        cli_args += self.encode_presets[self.codec]
        cli_args += [mov_path] if self.fps == 25 else ["-r", self.fps, mov_path]

        # TODO - PNG support
        if extension in (".png", ".PNG"):
            self.report({'WARNING'}, "PNG is not supported")
            return {"FINISHED"}

        """ Run ffmpeg """
        bpy.ops.loom.run_terminal(
            #debug_arguments=True,
            binary=prefs.ffmpeg_path,
            terminal_instance=self.terminal_instance,
            argument_collection=self.pack_arguments(cli_args),
            bash_name="loom-ffmpeg-temp",
            force_bash=prefs.bash_flag,
            pause=self.pause)

        self.report({'INFO'}, "Encoding {}{} to {}".format(filename_noext, extension, mov_path))
        return {"FINISHED"}

    def invoke(self, context, event):
        lum = context.scene.loom
        prefs = context.preferences.addons[__name__].preferences

        if not self.properties.is_property_set("codec"):
            if prefs.default_codec:
                try:
                    self.codec = prefs.default_codec
                except:
                    pass

        return context.window_manager.invoke_props_dialog(self, 
            width=(prefs.encode_dialog_width))

    def draw(self, context):
        lum = context.scene.loom
        prefs = context.preferences.addons[__name__].preferences
        layout = self.layout

        split_width = .2
        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Sequence:")
        col = split.column(align=True)
        sub = col.row(align=True)          
        sub.prop(lum, "sequence_encode", text="")
        if lum.sequence_encode:
            sub.operator(LOOM_OT_encode_verify_image_sequence.bl_idname, icon='GHOST_ENABLED', text="")
            sub.operator(LOOM_OT_open_folder.bl_idname, 
                icon="DISK_DRIVE", text="").folder_path = os.path.dirname(lum.sequence_encode)
        else:
            sub.operator(LOOM_OT_encode_auto_paths.bl_idname, text="", icon='AUTO') #GHOST_ENABLED, SEQUENCE
        sel_sequence = sub.operator(LOOM_OT_load_image_sequence.bl_idname, text="", icon='FILE_TICK')
        #sel_sequence.verify_sequence = False

        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Colorspace:")
        col = split.column(align=True)
        col.prop(self, "colorspace", text="")

        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Frame Rate:")
        col = split.column(align=True)
        col.prop(self, "fps", text="")

        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Codec:")
        col = split.column(align=True)
        col.prop(self, "codec", text="")

        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Movie File:")
        col = split.column(align=True)
        sub = col.row(align=True)
        sub.prop(lum, "movie_path", text="")
        if lum.movie_path:
            sub.operator(LOOM_OT_open_folder.bl_idname, 
                icon="DISK_DRIVE", text="").folder_path = os.path.dirname(lum.movie_path)
        sub.operator(LOOM_OT_encode_select_movie.bl_idname, text="", icon='FILE_TICK')
        
        if lum.lost_frames:
            layout.separator()
            spl = layout.split(factor=0.5)
            row = spl.row(align=True)
            row.prop(lum, "ignore_scene_range", text="", icon='RENDER_RESULT')
            fg = row.operator(LOOM_OT_fill_sequence_gaps.bl_idname, text="Fill Gaps with Copies")
            fg.sequence_path = lum.sequence_encode
            fg.scene_range = not lum.ignore_scene_range
            txt = "Render Missing Frames"
            di = spl.operator(LOOM_OT_render_input_dialog.bl_idname, icon='RENDER_STILL', text=txt)
            di.frame_input = lum.lost_frames
        layout.separator(factor=1.5)


class LOOM_OT_rename_dialog(bpy.types.Operator):
    """Rename Image or File Sequence"""
    bl_idname = "loom.rename_file_sequence"
    bl_label = "Rename File Sequence"
    bl_description = "Rename File or Image Sequence"
    bl_options = {'REGISTER'}
    bl_property = "new_name"

    sequence: bpy.props.StringProperty(
        name="Path to sequence",
        description="Path to sequence",
        maxlen=1024,
        subtype='FILE_PATH')
    
    new_name: bpy.props.StringProperty(
        name="Path to sequence",
        description="Path to sequence",
        maxlen=1024)
    
    keep_original_numbers: bpy.props.BoolProperty(
        name="Keep Original Numbers",
        description="Keep the Numbers of the Original File Sequence",
        default=False)

    start: bpy.props.IntProperty(
        name="Start at",
        description="Start at",
        default=1,
        min=0)
        
    open_file_browser: bpy.props.BoolProperty(
        name="Open File Browser",
        description="Open File Browser",
        default=True)
    
    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def determine_type(self, val): 
        #val = ast.literal_eval(s)
        if (isinstance(val, int)):
            return ("chi")
        elif (isinstance(val, float)):
            return ("chf")
        if val in ["true", "false"]:
            return ("chb")
        else:
            return ("chs")

    def number_suffix(self, filename):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)

    def pack_arguments(self, lst):
        return [{"idc": 0, "name": self.determine_type(i), "value": str(i)} for i in lst]

    def check(self, context):
        return True        

    def execute(self, context):
        lum = context.scene.loom
        image_sequence = {}
        seq_path = lum.sequence_encode if not self.sequence else self.sequence
        
        path_error = False
        if not seq_path:
            self.report({'ERROR'}, "No image sequence specified")
            path_error = True

        if path_error:
            bpy.ops.loom.rename_file_sequence('INVOKE_DEFAULT')
            return {"CANCELLED"}

        """ Verify image sequence """
        basedir, filename = os.path.split(seq_path)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, extension = os.path.splitext(filename)

        seq_error = False
        if '#' not in filename_noext:
            num_suff = self.number_suffix(filename_noext)
            if not num_suff:
                self.report({'ERROR'}, "No valid image sequence")
                seq_error = True
            else:
                filename_noext = filename_noext.replace(num_suff, "#"*len(num_suff))
        
        if not extension: # Sequence file format
            self.report({'ERROR'}, "File format not set (missing extension)")
            seq_error = True

        if seq_error:
            bpy.ops.loom.rename_file_sequence('INVOKE_DEFAULT')
            return {"CANCELLED"}

        hashes = filename_noext.count('#')
        name_real = filename_noext.replace("#", "")
        file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=extension)

        for f in os.scandir(basedir):
            if f.name.endswith(extension) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

        if not len(image_sequence) > 1:
            self.report({'WARNING'},"No valid image sequence")
            bpy.ops.loom.rename_file_sequence('INVOKE_DEFAULT')
            return {"CANCELLED"}

        """ Rename File Sequence """
        new_name = self.new_name
        if new_name.endswith(tuple(bpy.path.extensions_image)):
            new_name, file_extension = os.path.splitext(new_name)
        user_name = new_name.replace("#", "")
        user_hashes = new_name.count('#')
        if not user_hashes: user_hashes = hashes
        renamed = []

        # Rename the sequence temporary if already in place (windows issue)
        # -> os.rename fails in case the upcomming file has the same name
        if user_name == name_real and user_hashes == hashes:
            image_sequence_tmp = {}
            for c, (k, v) in enumerate(image_sequence.items(), start=1):
                num = "{n:0{dig}d}".format(n=c, dig=user_hashes)
                if self.keep_original_numbers:
                    d_tmp, fn_tmp = os.path.split(v)
                    num = "{n:0{dig}d}".format(n=int(self.number_suffix(fn_tmp)), dig=user_hashes)
                fp = os.path.join(basedir, "loom__tmp__{}{}".format(num, extension))
                os.rename(v, fp)
                image_sequence_tmp[int(num)] = fp
            image_sequence = image_sequence_tmp
        # -------------------------------------------------------------- */

        for c, (k, v) in enumerate(image_sequence.items(), start=self.start):
            num = "{n:0{dig}d}".format(n=c, dig=user_hashes)
            if self.keep_original_numbers:
                d_tmp, fn_tmp = os.path.split(v)
                num = "{n:0{dig}d}".format(n=int(self.number_suffix(fn_tmp)), dig=user_hashes)
            fp = os.path.join(basedir, "{}{}{}".format(user_name, num, extension))
            os.rename(v, fp)
            renamed.append(fp)
        
        if len(renamed) > 0:
            sn = "{}{}".format(user_name, '#'*user_hashes)
            lum.sequence_rename = "{}".format(sn)
            lum.sequence_encode = os.path.join(basedir, "{}{}".format(sn, extension))
            self.report({'INFO'}, "{} files renamed to {}".format(len(renamed), sn+extension))
            if self.open_file_browser:
                bpy.ops.loom.open_folder(folder_path=basedir)
        else:
            self.report({'ERROR'}, "Can not rename files")

        return {"FINISHED"}

    def invoke(self, context, event):
        prefs = context.preferences.addons[__name__].preferences
        self.new_name = context.scene.loom.sequence_rename
        return context.window_manager.invoke_props_dialog(self, 
            width=(prefs.encode_dialog_width))

    def draw(self, context):
        lum = context.scene.loom
        prefs = context.preferences.addons[__name__].preferences
        layout = self.layout

        split_width = .2
        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="Sequence:")
        col = split.column(align=True)
        sub = col.row(align=True)          
        sub.prop(lum, "sequence_encode", text="")
        if lum.sequence_encode:
            sub.operator(LOOM_OT_encode_verify_image_sequence.bl_idname, icon='GHOST_ENABLED', text="")
            sub.operator(LOOM_OT_open_folder.bl_idname, 
                icon="DISK_DRIVE", text="").folder_path = os.path.dirname(lum.sequence_encode)
        else:
            sub.operator(LOOM_OT_encode_auto_paths.bl_idname, text="", icon='AUTO') #GHOST_ENABLED, SEQUENCE
        sel_sequence = sub.operator(LOOM_OT_load_image_sequence.bl_idname, text="", icon='FILE_TICK')
        sel_sequence.dialog = 'rename'
        sel_sequence.verify_sequence = False

        split = layout.split(factor=split_width)
        col = split.column(align=True)
        col.label(text="New Sequence Name:")
        col = split.column(align=True)
        split = col.split(factor=0.85)
        split.prop(self, "new_name", text="")
        row = split.row(align=True)
        row.prop(self, "keep_original_numbers", text="", icon='TEMP')
        col = row.column(align=True)
        col.enabled = not self.keep_original_numbers
        col.prop(self, "start", text="")
        layout.row()
        layout.row().prop(self, "open_file_browser")
        layout.separator()


class LOOM_OT_load_image_sequence(bpy.types.Operator, ImportHelper):
    """Select File of Image Sequence"""
    bl_idname = "loom.load_sequence"
    bl_label = "Select File of Image Sequence"
    bl_options = {'INTERNAL'}
    
    cursor_pos = [0,0]
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    dialog: bpy.props.EnumProperty(
        name="Dialog",
        options={'HIDDEN'},
        items=(
            ("encode", "Encode Dialog", "", 1),
            ("rename", "Rename Dialog", "", 2)))

    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.jpg;*.exr;*dpx;*tga;*tif;*tiff;",
        #default="*" + ";*".join(bpy.path.extensions_image),
        options={'HIDDEN'})

    verify_sequence: bpy.props.BoolProperty(
            name="Verify Image Sequence",
            description="Detects missing frames",
            default=True,
            options={'SKIP_SAVE'})
    
    scene_range: bpy.props.BoolProperty(
            name="Scene Range",
            description="Consider the Frames of the Scene",
            default=False,
            #options={'SKIP_SAVE'}
            )

    def number_suffix(self, filename):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)
    
    def bound_frame(self, frame_path, frame_iter):
        folder, filename = os.path.split(frame_path)
        digits = self.number_suffix(filename)
        frame = re.sub('\d(?!\d)', lambda x: str(int(x.group(0)) + frame_iter), digits)
        return os.path.exists(os.path.join(folder, frame.join(filename.rsplit(digits))))

    def is_sequence(self, filepath):
        folder, filename = os.path.split(filepath) # any(char.isdigit() for char in filename)
        filename_noext, ext = os.path.splitext(filename)
        if not filename_noext[-1].isdigit(): return False
        next_frame = self.bound_frame(filepath, 1)
        prev_frame = self.bound_frame(filepath, -1)
        return True if next_frame or prev_frame else False

    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))
    
    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        #G=([list(x) for _,x in groupby(L, lambda x,c=count(): next(c)-x)])
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def display_popup(self, context):
        win = context.window #win.cursor_warp((win.width*.5)-100, (win.height*.5)+100)
        win.cursor_warp(x=self.cursor_pos[0], y=self.cursor_pos[1]+100) # x-100 y-+70
        if self.dialog == 'encode':
            bpy.ops.loom.encode_dialog('INVOKE_DEFAULT') # re-invoke the dialog
        if self.dialog == 'rename':
            bpy.ops.loom.rename_file_sequence('INVOKE_DEFAULT') # re-invoke the dialog

    @classmethod
    def poll(cls, context):
        return True #context.object is not None

    def execute(self, context):
        lum = context.scene.loom
        image_sequence = {}

        basedir, filename = os.path.split(self.filepath)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)
        frame_suff = self.number_suffix(filename)

        if not os.path.isfile(self.filepath):
            self.report({'WARNING'},"Please select one image of an image sequence")
            self.display_popup(context)
            return {"CANCELLED"}

        if not frame_suff:
            self.report({'WARNING'},"No valid image sequence")
            self.display_popup(context)
            return {"CANCELLED"}

        sequence_name = filename.replace(frame_suff,'#'*len(frame_suff))
        sequence_path = os.path.join(basedir, sequence_name)
        name_real = filename_noext.replace(frame_suff, "")

        """ Verify image sequence on disk (Scan directory) """
        if self.verify_sequence:
            hashes = sequence_name.count('#')
            file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=ext)
            for f in os.scandir(basedir):
                if f.name.endswith(ext) and f.is_file():
                    match = re.match(file_pattern, f.name, re.IGNORECASE)
                    if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

            if not len(image_sequence) > 1:
                self.report({'WARNING'},"No valid image sequence")
                return {"CANCELLED"}

            """ Detect missing frames """  #start_frame, end_frame = fn[0], fn[-1]
            frame_numbers = sorted(list(image_sequence.keys()))
            missing_frame_list = self.missing_frames(frame_numbers)

            if frame_numbers and self.scene_range:
                scn = context.scene
                missing_frame_list += range(scn.frame_start, frame_numbers[0])
                missing_frame_list += range(frame_numbers[-1]+1, scn.frame_end+1)
                missing_frame_list = sorted(missing_frame_list)

            if missing_frame_list:
                lum.lost_frames = self.rangify_frames(missing_frame_list)
                context.window_manager.clipboard = "{}".format(
                    ','.join(map(str, missing_frame_list)))
                error_massage = "Missing frames detected: {}".format(self.rangify_frames(missing_frame_list))
                self.report({'ERROR_INVALID_INPUT'}, error_massage)
                self.report({'ERROR'},"Frame list copied to clipboard.")
            else:
                lum.lost_frames = ""
                self.report({'INFO'},"Valid image sequence, Frame range: {}".format(
                    self.rangify_frames(frame_numbers)))

        else:
            """ Quick test whether single image or not """
            if not self.is_sequence(self.filepath):
                self.report({'WARNING'},"No valid image sequence") #return {"CANCELLED"}
            else:
                lum.lost_frames = ""

        if self.dialog == 'encode':
            if not name_real: name_real = "untitled"
            name_real = name_real[:-1] if name_real.endswith(("-", "_")) else name_real
            lum.movie_path = os.path.join(basedir, name_real + ".mov")
        if self.dialog == 'rename':
            lum.sequence_rename = name_real
        lum.sequence_encode = sequence_path

        self.display_popup(context)
        return {'FINISHED'}
    
    def cancel(self, context):
        self.display_popup(context)

    def invoke(self, context, event):
        s = context.scene.loom.sequence_encode
        self.filepath = os.path.dirname(s) + "/" if s else bpy.path.abspath("//")
        self.cursor_pos = [event.mouse_x, event.mouse_y]
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class LOOM_OT_encode_select_movie(bpy.types.Operator, ImportHelper):
    """Movie file path"""
    bl_idname = "loom.save_movie"
    bl_label = "Save Movie File"
    bl_options = {'INTERNAL'}
    
    cursor_pos = [0,0]
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")    
    filename: bpy.props.StringProperty()    
    
    filename_ext = ".mov"
    filter_glob: bpy.props.StringProperty(
            default="*.mov;",
            options={'HIDDEN'})
    
    def name_from_sequence(self, context):
        lum = context.scene.loom
        basedir, filename = os.path.split(lum.sequence_encode)
        filename_noext, ext = os.path.splitext(filename)
        name_real = filename_noext.replace('#', "")
        if name_real.endswith(("-", "_")):
            name_real = name_real[:-1]
        return "{}.mov".format(name_real)

    def display_popup(self, context):
        win = context.window #win.cursor_warp((win.width*.5)-100, (win.height*.5)+100)
        win.cursor_warp(x=self.cursor_pos[0], y=self.cursor_pos[1]+100)
        bpy.ops.loom.encode_dialog('INVOKE_DEFAULT') # re-invoke the dialog
        
    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        lum = context.scene.loom
        folder, file = os.path.split(self.filepath)
        filename, ext = os.path.splitext(file)
        if os.path.isdir(self.filepath):
            filename = "untitled"
        if ext != ".mov":
            lum.movie_path = os.path.join(folder, "{}{}.mov".format(filename,ext))
        else:
            lum.movie_path = self.filepath #self.report({'WARNING'},"No valid file type")
        self.display_popup(context)
        return {'FINISHED'}
    
    def cancel(self, context):
        self.display_popup(context)

    def invoke(self, context, event):
        self.filename = self.name_from_sequence(context)
        self.cursor_pos = [event.mouse_x, event.mouse_y]
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class LOOM_OT_encode_verify_image_sequence(bpy.types.Operator):
    """Verify & Refresh Image Sequence"""
    bl_idname = "loom.image_sequence_verify"
    bl_label = "Verify Image Sequence"
    bl_options = {'INTERNAL'}

    scene_range: bpy.props.BoolProperty(
        name="Scene Range",
        description="Consider the Frames of the Scene",
        default=True,
        options={'SKIP_SAVE'}
        )

    def rangify_frames(self, frames):
        """ Convert list of integers to Range string [1,2,3] -> '1-3' """
        G=(list(x) for _,x in groupby(frames, lambda x,c=count(): next(c)-x))
        return ",".join("-".join(map(str,(g[0],g[-1])[:len(g)])) for g in G)

    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def number_suffix(self, filename):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)

    def execute(self, context):
        lum = context.scene.loom
        image_sequence = {}

        if not lum.sequence_encode:
            self.report({'WARNING'},"No image sequence specified")
            return {"CANCELLED"}

        basedir, filename = os.path.split(lum.sequence_encode)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)
        
        seq_error = False
        if '#' not in filename_noext:
            num_suff = self.number_suffix(filename_noext)
            if num_suff:
                filename_noext = filename_noext.replace(num_suff, "#"*len(num_suff))
                sequence_name = "{}{}".format(filename_noext, ext)
                lum.sequence_encode = "{}".format(os.path.join(basedir, sequence_name))
            else:
                seq_error = True

        if seq_error:
            self.report({'ERROR'},"No valid image sequence")
            return {"CANCELLED"}
        if not ext:
            self.report({'ERROR'}, "File format not set (missing extension)")
            return {"CANCELLED"}

        hashes = filename_noext.count('#')
        name_real = filename_noext.replace("#", "")
        file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=ext)

        for f in os.scandir(basedir):
            if f.name.endswith(ext) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

        if not len(image_sequence) > 1:
            self.report({'ERROR'},"Specified image sequence not found on disk")
            return {"CANCELLED"}

        """ Detect missing frames """
        frame_numbers = sorted(list(image_sequence.keys()))
        missing_frame_list = self.missing_frames(frame_numbers)
        msg = "(based on the image sequence found on disk)"

        if frame_numbers and self.scene_range:
            scn = context.scene
            missing_frame_list += range(scn.frame_start, frame_numbers[0])
            missing_frame_list += range(frame_numbers[-1]+1, scn.frame_end+1)
            missing_frame_list = sorted(missing_frame_list)
            msg = "(based on the frame range of the scene)"

        if missing_frame_list:
            lum.lost_frames = self.rangify_frames(missing_frame_list)
            context.window_manager.clipboard = "{}".format(
                ','.join(map(str, missing_frame_list)))
            error_massage = "Missing frames detected {}: {}".format(msg, self.rangify_frames(missing_frame_list))
            self.report({'ERROR'}, error_massage)
            self.report({'ERROR'},"Frame list copied to clipboard.")
        else:
            self.report({'INFO'},'Sequence: "{}{}" found on disk, Frame range: {}'.format(
                filename_noext, ext, self.rangify_frames(frame_numbers)))
            lum.lost_frames = ""
        return {'FINISHED'}
    
    def invoke(self, context, event):
        if event.alt or event.ctrl:
            self.scene_range = False
        return self.execute(context)


class LOOM_OT_encode_auto_paths(bpy.types.Operator):
    """Auto Paths based on the latest Loom render (hold Ctrl to force the use of the default path)"""
    bl_idname = "loom.encode_auto_paths"
    bl_label = "Set sequence and movie path automatically"
    bl_options = {'INTERNAL'}

    default_path: bpy.props.BoolProperty(
        name="Default Output Path",
        description="Use the default Output Path",
        default=False,
        options={'SKIP_SAVE'})

    def number_suffix(self, filename):
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)

    @classmethod
    def poll(cls, context):
        return not context.scene.loom.sequence_encode

    def execute(self, context):
        lum = context.scene.loom
        basedir, filename = os.path.split(context.scene.render.frame_path(frame=0))
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)
        num_suff = self.number_suffix(filename_noext)
        report_msg = "Sequence path set based on default output path"

        if lum.render_collection and not self.default_path:
            latest_frame = lum.render_collection[-1]
            basedir = os.path.dirname(bpy.path.abspath(replace_globals(latest_frame.file_path))) # Absolute or Relative?
            num_suff = "0".zfill(latest_frame.padded_zeros)
            filename_noext = replace_globals(latest_frame.name) + num_suff
            ext = ".{}".format(latest_frame.image_format)
            report_msg = "Sequence path set based on latest Loom render"
        
        if not lum.movie_path:
            movie_noext = filename_noext.replace(num_suff, "")
            movie_noext = movie_noext[:-1] if movie_noext.endswith(("-", "_", ".")) else movie_noext
            lum.movie_path = "{}.mov".format(bpy.path.abspath(os.path.join(basedir, movie_noext)))

        filename_noext = filename_noext.replace(num_suff, "#"*len(num_suff))
        sequence_name = "{}{}".format(filename_noext, ext)
        lum.sequence_encode = "{}".format(os.path.join(basedir, sequence_name))
        self.report({'INFO'}, report_msg)
        return {'FINISHED'}
    
    def invoke(self, context, event):
        if event.alt or event.ctrl:
            self.default_path = True
        return self.execute(context)


class LOOM_OT_fill_sequence_gaps(bpy.types.Operator):
    """Fill gaps in image sequence with copies of existing frames"""
    bl_idname = "loom.fill_image_sequence"
    bl_label = "Fill gaps in image sequence with copies of previous frames?"
    bl_options = {'INTERNAL'}
    
    sequence_path: bpy.props.StringProperty()
    scene_range: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    def re_path(self, basedir, name_real, frame, hashes, extension):
        return os.path.join(
            basedir, 
            "{n}{f:0{h}d}{e}".format(n=name_real, f=frame, h=hashes, e=extension)
            )

    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def execute(self, context):
        lum = context.scene.loom
        image_sequence = {}

        basedir, filename = os.path.split(self.sequence_path)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)

        if "#" not in filename_noext:
            self.report({'WARNING'},"No valid image sequence")
            return {"CANCELLED"}

        """ Scan directory """
        hashes = filename_noext.count('#')
        name_real = filename_noext.replace("#", "")
        file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=name_real, ds=hashes, ex=ext)
        for f in os.scandir(basedir):
            if f.name.endswith(ext) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

        if not len(image_sequence) > 1:
            self.report({'WARNING'},"No valid image sequence")
            return {"CANCELLED"}

        """ Assemble missing frames """
        frame_numbers = sorted(list(image_sequence.keys())) 
        #start_frame, end_frame = fn[0], fn[-1]
        missing_frame_list = self.missing_frames(frame_numbers)
        frames_to_copy = {}

        if missing_frame_list:
            f_prev = frame_numbers[0]
            for frame in range(frame_numbers[0], frame_numbers[-1]+1):
                if frame not in image_sequence:
                    path_copy = self.re_path(basedir, name_real, frame, hashes, ext)
                    frames_to_copy.setdefault(image_sequence[f_prev], []).append(path_copy)
                else:
                    f_prev = frame

        """ Extend to frame range of the scene """
        if self.scene_range:
            for i in range(context.scene.frame_start, frame_numbers[0]):
                path_copy = self.re_path(basedir, name_real, i, hashes, ext)
                frames_to_copy.setdefault(image_sequence[frame_numbers[0]], []).append(path_copy)
            
            for o in range(frame_numbers[-1]+1, context.scene.frame_end+1):
                path_copy = self.re_path(basedir, name_real, o, hashes, ext)
                frames_to_copy.setdefault(image_sequence[frame_numbers[-1]], []).append(path_copy)
        
        """ Copy the Images """
        if frames_to_copy:
            try:
                from shutil import copyfile
                for src, dest in frames_to_copy.items():
                        for ff in dest:
                            copyfile(src, ff)
                self.report({'INFO'},"Successfully copied all missing frames")
                #if self.options.is_invoke:
                lum.lost_frames = ""
            except OSError:
                self.report({'ERROR'}, "Error while trying to copy frames")
        else:
            self.report({'INFO'},"No Gaps, nothing to do")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


class LOOM_OT_open_folder(bpy.types.Operator):
    """Opens a certain Folder in the File Browser"""
    bl_idname = "loom.open_folder"
    bl_label = "Open Folder"
    bl_options = {'INTERNAL'}
    
    folder_path: bpy.props.StringProperty()
    
    def execute(self, context):
        fp = self.folder_path
        glob_vars = context.preferences.addons[__name__].preferences.global_variable_coll
        if any(ext in fp for ext in glob_vars.keys()):
            fp = replace_globals(fp)
        
        fp = os.path.realpath(bpy.path.abspath(fp))
        if os.path.isfile(fp) or not os.path.exists(fp):
            fp = os.path.dirname(fp)
        if not os.path.isdir(fp):
            self.report({'INFO'}, "'{}' no folder".format(fp))
            return {"CANCELLED"}
        try:
            if platform.startswith('darwin'):
                webbrowser.open("file://{}".format(fp))
            elif platform.startswith('linux'):
                try:
                    #os.system('xdg-open "{}"'.format(fp))
                    subprocess.call(["xdg-open", fp])
                except:
                    webbrowser.open(fp)
            else:
                webbrowser.open(fp)
        except OSError:
            self.report({'INFO'}, "'{}' does not exist".format(fp))
        return {'FINISHED'}


class LOOM_OT_open_output_folder(bpy.types.Operator):
    """Open up the Output Directory in the File Browser"""
    bl_idname = "loom.open_ouput_dir"
    bl_label = "Open Output Directory"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        fp = context.scene.render.filepath
        glob_vars = context.preferences.addons[__name__].preferences.global_variable_coll
        if any(ext in fp for ext in glob_vars.keys()):
            fp = replace_globals(fp)

        fp = os.path.realpath(bpy.path.abspath(fp))
        if not os.path.isdir(fp):
            fp = os.path.dirname(fp)
        if os.path.isdir(fp):
            bpy.ops.loom.open_folder(folder_path=fp)
        else:
            bpy.ops.loom.open_folder(folder_path=bpy.path.abspath("//"))
            self.report({'INFO'}, "Folder does not exist")
        return {'FINISHED'}


class LOOM_OT_utils_node_cleanup(bpy.types.Operator):
    """Remove version strings from File Output Nodes"""
    bl_idname = "loom.remove_version_strings"
    bl_label = "Remove Version Strings from File Output Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # space = context.space_data return space.type == 'NODE_EDITOR'
        # all([hasattr(scene.node_tree, "nodes"), scene.render.use_compositing, scene.use_nodes])
        return hasattr(context.scene.node_tree, "nodes")
        
    def remove_version(self, fpath):
        match = re.search(r'(v\d+)', fpath)
        delimiters = ("-", "_", ".")
        if match:
            head, tail = fpath.split(match.group(0))
            if tail.startswith(delimiters):
                tail = tail[1:]
            fpath = head + tail
            return fpath[:-1] if fpath.endswith(delimiters) else fpath
        else:
            return fpath
    
    def execute(self, context):
        scene = context.scene    
        nodes = scene.node_tree.nodes
        output_nodes = [n for n in nodes if n.type=='OUTPUT_FILE']
        
        if not output_nodes:
            self.report({'INFO'}, "Nothing to operate on")
            return {'CANCELLED'}
            
        for out_node in output_nodes:
            if "LAYER" in out_node.format.file_format:
                out_node.base_path = self.remove_version(out_node.base_path)
                for layer in out_node.layer_slots:
                    layer.name = self.remove_version(layer.name)
            else:
                out_node.base_path = self.remove_version(out_node.base_path)
                for out_file in out_node.file_slots:
                    out_file.path = self.remove_version(out_file.path)
            
            scene.loom.output_sync_comp=False
        return {'FINISHED'}


class LOOM_OT_open_preferences(bpy.types.Operator):
    """Loom Preferences Window"""
    bl_idname = "loom.open_preferences"
    bl_label = "Loom Preferences"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.preferences.addon_show(module="loom")
        return {'FINISHED'}
        
        
class LOOM_OT_openURL(bpy.types.Operator):
    """Open URL in default Browser"""
    bl_idname = "loom.open_url"
    bl_label = "Documentation"
    bl_options = {'INTERNAL'}
    
    url: bpy.props.StringProperty(name="URL")
    description: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return properties.description

    def execute(self, context):
        webbrowser.open_new(self.url)
        return {'FINISHED'}


# -------------------------------------------------------------------
#    Rendering Operators
# -------------------------------------------------------------------

class LOOM_OT_render_terminal(bpy.types.Operator):
    """Render image sequence in terminal instance"""
    bl_idname = "loom.render_terminal"
    bl_label = "Render Image Sequence in Terminal Instance"
    bl_options = {'REGISTER', 'INTERNAL'}

    frames: bpy.props.StringProperty(
        name="Frames",
        description="Specify a range or frames to render")

    threads: bpy.props.IntProperty(
        name="CPU Threads",
        description="Number of CPU threads to use simultaneously while rendering",
        min = 1)

    digits: bpy.props.IntProperty(
        name="Digits",
        description="Specify digits in filename",
        default=4)

    isolate_numbers: bpy.props.BoolProperty(
        name="Filter Raw Items",
        description="Filter raw elements in frame input",
        default=False)

    render_preset: bpy.props.StringProperty(
        name="Render Preset",
        description="Pass a custom Preset.py")

    debug: bpy.props.BoolProperty(
        name="Debug Arguments",
        description="Print full argument list",
        default=False)

    def determine_type(self, val):
        if (isinstance(val, int)):
            return ("chi")
        elif (isinstance(val, float)):
            return ("chf")
        if val in ["true", "false"]:
            return ("chb")
        else:
            return ("chs")

    def pack_arguments(self, lst):
        return [{"idc": 0, "name": self.determine_type(i), "value": str(i)} for i in lst]

    @classmethod
    def poll(cls, context):
        return not context.scene.render.is_movie_format

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences

        if bpy.data.is_dirty: # Save latest changes
            bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)

        python_expr = ("import bpy;" +\
                "bpy.ops.render.image_sequence(" +\
                "frames='{fns}', isolate_numbers={iel}," +\
                "render_silent={cli}, digits={lzs}, render_preset='{pst}')").format(
                    fns=self.frames,
                    iel=self.isolate_numbers, 
                    cli=True, 
                    lzs=self.digits,
                    pst=self.render_preset)

        cli_args = ["-b", bpy.data.filepath, "--python-expr", python_expr]
        
        if self.properties.is_property_set("threads"):
            cli_args = cli_args + ["-t", "{}".format(self.threads)]

        bpy.ops.loom.run_terminal( 
            debug_arguments=self.debug,
            terminal_instance=True,
            argument_collection=self.pack_arguments(cli_args), 
            bash_name="loom-render-temp",
            force_bash = prefs.bash_flag)

        return {"FINISHED"}


class LOOM_OT_render_image_sequence(bpy.types.Operator):
    """Render image sequence either in background or within the UI"""
    bl_idname = "render.image_sequence"
    bl_label = "Render Image Sequence"
    bl_options = {'REGISTER', 'INTERNAL'}

    frames: bpy.props.StringProperty(
        name="Frames",
        description="Specify a range or single frames to render")

    isolate_numbers: bpy.props.BoolProperty(
        name="Filter Raw Items",
        description="Filter raw elements in frame input",
        default=False)

    render_silent: bpy.props.BoolProperty(
        name="Render silent",
        description="Render without displaying the progress within the UI",
        default=False)

    digits: bpy.props.IntProperty(
        name="Digits",
        description="Specify digits in filename",
        default=4)
    
    render_preset: bpy.props.StringProperty(
        name="Render Preset",
        description="Pass a custom Preset.py")

    _image_formats = {'BMP': 'bmp', 'IRIS': 'iris', 'PNG': 'png', 'JPEG': 'jpg', 
        'JPEG2000': 'jp2', 'TARGA': 'tga', 'TARGA_RAW': 'tga', 'CINEON': 'cin', 
        'DPX': 'dpx', 'OPEN_EXR_MULTILAYER': 'exr', 'OPEN_EXR': 'exr', 'HDR': 'hdr', 
        'TIFF': 'tif', 'WEBP': 'webp', 'SUPPLEMENT1': 'tiff', 'SUPPLEMENT2': 'jpeg'}

    _rendered_frames, _skipped_frames = [], []
    _timer = _frames = _stop = _rendering = _dec = _log = None
    _output_path = _folder = _filename = _extension = None
    _subframe_flag = False
    _output_nodes = {}
    
    @classmethod
    def poll(cls, context):
        return not context.scene.render.is_movie_format

    def pre_render(self, scene, depsgraph):
        self._rendering = True
        scene.loom.is_rendering = True

    def cancel_render(self, scene, depsgraph):
        self._stop = True
        self.reset_output_paths(scene)
        self._rendered_frames.pop()

    def post_render(self, scene, depsgraph):
        self._frames.pop(0)
        self._rendering = False
        scene.loom.is_rendering = False
        
    def file_extension(self, file_format):
        return self._image_formats[file_format]
    
    def subframes(self, sub_frames):
        subs = []
        for frame in sub_frames:
            main_frame, sub_frame = repr(frame).split('.')
            subs.append((int(main_frame), float('.' + sub_frame)))
        return subs

    def format_frame(self, file_name, frame, extension=None):
        file_name = replace_globals(file_name)
        if extension:
            return "{f}{fn:0{lz}d}.{ext}".format(
                f=file_name, fn=frame, lz=self.digits, ext=extension)
        else:
            return "{f}{fn:0{lz}d}_".format(
                f=file_name, fn=frame, lz=self.digits)

    def format_subframe(self, file_name, frame, extension=None):
        file_name = replace_globals(file_name)
        sub_frame = "{sf:.{dec}f}".format(sf = frame[1], dec=self._dec).split('.')[1]
        if extension:
            return "{f}{mf:0{lz}d}{sf}.{ext}".format(
                f=file_name, mf=frame[0], lz=self.digits, 
                sf=sub_frame, ext=extension)
        else:
            return "{f}{mf:0{lz}d}{sf}_".format(
                f=file_name, mf=frame[0], lz=self.digits, sf=sub_frame)

    def safe_filename(self, file_name):
        if file_name:
            if file_name.lower().endswith(tuple(self._image_formats.values())):
                name_real, ext = os.path.splitext(file_name)
            else:
                name_real = file_name
            if "#" in name_real:
                hashes = re.findall("#+$", name_real)
                name_real = re.sub("#", '', name_real)
                self.digits = len(hashes[0]) if hashes else 4
            return name_real + "_" if name_real and name_real[-1].isdigit() else name_real
        
        else: # If filename not specified, use blend-file name instead
            blend_name, ext = os.path.splitext(os.path.basename(bpy.data.filepath))
            return blend_name + "_"

    def out_nodes(self, scene):
        tree = scene.node_tree
        return [n for n in tree.nodes if n.type=='OUTPUT_FILE'] if tree else []

    def reset_output_paths(self, scene):
        scene.render.filepath = self._output_path
        for k, v in self._output_nodes.items():
            k.base_path = v["Base Path"]
            if "File Slots" in v: # Reset Slots
                for c, fs in enumerate(k.file_slots):
                    fs.path = v["File Slots"][c]

    def frame_repath(self, scene, frame_number):
        ''' Set the frame, assamble main file and output node paths '''
        if self._subframe_flag:
            scene.frame_set(frame_number[0], subframe=frame_number[1])
            ff = self.format_subframe(self._filename, frame_number, self._extension)
        else:
            scene.frame_set(frame_number)
            ff = self.format_frame(self._filename, frame_number, self._extension)
        
        """ Final main path assembly """
        scene.render.filepath = os.path.join(self._folder, ff)
                
        for k, v in self._output_nodes.items():
            if "File Slots" in v:
                k.base_path = replace_globals(k.base_path)
                for c, f in enumerate(k.file_slots):
                    if self._subframe_flag:
                        f.path = self.format_subframe(v["File Slots"][c], frame_number)
                    else:
                        #f.path = self.format_frame(v["File Slots"][c], frame_number)
                        f.path = replace_globals(v["File Slots"][c])
            else:
                if self._subframe_flag:
                    of = self.format_subframe(v["Filename"], frame_number)
                else:
                    #of = self.format_frame(v["Filename"], frame_number)
                    of = replace_globals(v["Filename"])
                """ Final output node path assembly """
                k.base_path = os.path.join(replace_globals(v["Folder"]), of)

    def start_render(self, scene, frame, silent=False):
        rndr = scene.render # Skip frame, if rendered already
        if not rndr.use_overwrite and os.path.isfile(rndr.filepath):
            self._skipped_frames.append(frame)
            self.post_render(scene, None)
        else:
            if silent:
                bpy.ops.render.render(write_still=True)
            else:
                bpy.ops.render.render("INVOKE_DEFAULT", write_still=True)
            if frame not in self._rendered_frames:
                self._rendered_frames.append(frame)

    def log_sequence(self, scene, limit):
        from time import ctime #lum.render_collection.clear()
        lum = scene.loom
        if len(lum.render_collection) == limit:
            lum.render_collection.remove(0)
        render = lum.render_collection.add()
        render.render_id = len(lum.render_collection)
        render.start_time = ctime()
        render.start_frame = str(self._frames[0])
        render.end_frame = str(self._frames[-1])
        render.name = self._filename
        render.file_path = self._output_path
        render.padded_zeros = self.digits if not self._dec else self.digits + self._dec
        render.image_format = self._extension

    def final_report(self):
        if self._rendered_frames:
            frame_count = len(self._rendered_frames)
            if isinstance(self._rendered_frames[0], tuple):
                rendered = ', '.join("{mf}.{sf}".format(
                    mf=i[0], sf=str(i[1]).split(".")[1]) for i in self._rendered_frames)
            else:
                rendered = ','.join(map(str, self._rendered_frames))
            self.report({'INFO'}, "{} {} rendered.".format(
                "Frames:" if frame_count > 1 else "Frame:", rendered))
            self.report({'INFO'}, "{} saved to {}".format(
                "Images" if frame_count > 1 else "Image", self._folder))
                
        if self._skipped_frames:
            if isinstance(self._skipped_frames[0], tuple):
                skipped = ', '.join("{mf}.{sf}".format(
                    mf=i[0], sf=str(i[1]).split(".")[1]) for i in self._skipped_frames)
            else:
                skipped = ','.join(map(str, self._skipped_frames))
            self.report({'WARNING'}, "{} skipped (would overwrite existing file(s))".format(skipped))

    def execute(self, context):
        scn = context.scene
        prefs = context.preferences.addons[__name__].preferences
        glob_vars = prefs.global_variable_coll
    
        """ Filter user input """
        self._frames = filter_frames(self.frames, scn.frame_step, self.isolate_numbers)

        if not self._frames:
            self.report({'INFO'}, "No frames to render")
            return {"CANCELLED"}
        
        if not self.render_silent:
            self.report({'INFO'}, "Rendering Image Sequence...\n")

        """ Main output path """        
        self._output_path = scn.render.filepath
        output_folder, self._filename = os.path.split(bpy.path.abspath(self._output_path))
        self._folder = os.path.realpath(output_folder)        
        self._extension = self.file_extension(scn.render.image_settings.file_format)
        self._filename = self.safe_filename(self._filename)
        #self._output_path = os.path.join(self._folder, self._filename)

        # Replace globals in main output path
        if any(ext in self._folder for ext in glob_vars.keys()):
            self._folder = replace_globals(self._folder)
            bpy.ops.loom.create_directory(directory=self._folder)
            if not os.path.isdir(self._folder):
                self.report({'INFO'}, "Specified folder can not be created")
                return {"CANCELLED"}

        """ Output node paths """
        for out_node in self.out_nodes(scn):
            fd, fn = os.path.split(bpy.path.abspath(out_node.base_path))
            self._output_nodes[out_node] = {
                "Type": out_node.format.file_format,
                "Extension": self.file_extension(out_node.format.file_format),
                "Base Path": out_node.base_path,
                "Folder": os.path.realpath(fd),
                "Filename": fn}

            """ Single file slots in case """
            if not "LAYER" in out_node.format.file_format:
                self._output_nodes[out_node].update({"File Slots": [s.path for s in out_node.file_slots]})
                #"File Slots": {s.path : self.safe_filename(s.path) for s in out_node.file_slots}
        
        """ Clear assigned frame numbers """
        self._skipped_frames.clear(), self._rendered_frames.clear()

        """ Determine whether given frames are subframes """
        if isinstance(self._frames[0], float):
            self._frames = self.subframes(self._frames)
            self._dec = max(map(lambda x: len(str(x[1]).split('.')[1]), self._frames))
            self._subframe_flag = True

        """ Logging """
        if prefs.log_render: self.log_sequence(scn, prefs.log_render_limit)
        
        """ Render silent """
        if self.render_silent:

            """ Apply custom Render Preset """
            if self.render_preset and self.render_preset != "EMPTY":
                bpy.ops.script.execute_preset(
                    filepath=os.path.join(prefs.render_presets_path,self.render_preset),
                    menu_idname=LOOM_PT_render_presets.__name__)
            
            for frame_number in self._frames:
                self.frame_repath(scn, frame_number)
                self.start_render(scn, frame_number, silent=True)

            """ Reset output path & display results """
            self.reset_output_paths(scn)
            return {"FINISHED"}

        """ Add timer & handlers for modal """
        if not self.render_silent:
            self._stop = False
            self._rendering = False
            bpy.app.handlers.render_pre.append(self.pre_render)
            bpy.app.handlers.render_post.append(self.post_render)
            bpy.app.handlers.render_cancel.append(self.cancel_render)
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.3, window=context.window)
            wm.modal_handler_add(self)
            return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == 'TIMER':
            scn = context.scene

            """ Determine whether frame list is empty or process is interrupted by the user """
            if not self._frames or self._stop: #if True in (not self._frames, self._stop is True):
                context.window_manager.event_timer_remove(self._timer)
                bpy.app.handlers.render_pre.remove(self.pre_render)
                bpy.app.handlers.render_post.remove(self.post_render)
                bpy.app.handlers.render_cancel.remove(self.cancel_render)

                """ Reset output path & display results """
                self.reset_output_paths(scn)
                self.final_report()
                return {"FINISHED"}

            elif self._rendering is False:
                """ Render within UI & show the progress as usual """
                if self._frames:
                    frame_number = self._frames[0]
                    self.frame_repath(scn, frame_number)
                    self.start_render(scn, frame_number, silent=False)

        return {"PASS_THROUGH"}


# -------------------------------------------------------------------
#    Playblast (Experimental)
# -------------------------------------------------------------------

class LOOM_OT_playblast(bpy.types.Operator):
    """Playback rendered image sequence using the default or blender player"""
    bl_idname = "loom.playblast"
    bl_label = "Playblast Sequence"
    bl_options = {'REGISTER', 'INTERNAL'}
    
    # Todo! Just a temporary solution.
    # Might be a better idea trying to implement properties
    # for bpy.ops.render.play_rendered_anim() operator,
    # /startup/bl_operators/screen_play_rendered.py
    _image_sequence = {}

    def is_sequence(self, filepath):
        next_frame = re.sub('\d(?!\d)', lambda x: str(int(x.group(0)) + 1), filepath)
        return True if os.path.exists(next_frame) else False

    def number_suffix(self, filename):
        # test whether last char is digit?
        regex = re.compile(r'\d+\b')
        digits = ([x for x in regex.findall(filename)])
        return next(reversed(digits), None)

    def missing_frames(self, frames):
        return sorted(set(range(frames[0], frames[-1] + 1)).difference(frames))

    def file_sequence(self, filepath, digits=None, extension=None):
        basedir, filename = os.path.split(filepath)
        basedir = os.path.realpath(bpy.path.abspath(basedir))
        filename_noext, ext = os.path.splitext(filename)
        num_suffix = self.number_suffix(filename_noext)
        filename = filename_noext.replace(num_suffix,'') if num_suffix else filename_noext
        if extension: ext = extension
        if digits:
            file_pattern = r"{fn}(\d{{{ds}}})\.?{ex}$".format(fn=filename, ds=digits, ex=ext)
        else:
            file_pattern = r"{fn}(\d+)\.?{ex}".format(fn=filename, ex=ext)
        
        for f in os.scandir(basedir):
            if f.name.endswith(ext) and f.is_file():
                match = re.match(file_pattern, f.name, re.IGNORECASE)
                if match: self._image_sequence[int(match.group(1))] = os.path.join(basedir, f.name)

    def determine_type(self, val): 
        #val = ast.literal_eval(s)
        if (isinstance(val, int)):
            return ("chi")
        elif (isinstance(val, float)):
            return ("chf")
        if val in ["true", "false"]:
            return ("chb")
        else:
            return ("chs")

    def pack_arguments(self, lst):
        return [{"idc": 0, "name": self.determine_type(i), "value": str(i)} for i in lst]

    def execute(self, context):
        scn = context.scene
        lum = scn.loom
        prefs = context.preferences.addons[__name__].preferences #prefs.user_player = True
        glob_vars = prefs.global_variable_coll
        preview_filetype = "jpg" if scn.render.image_settings.use_preview else None
        default_flag = False
        sequence_name = None

        if len(lum.render_collection) > 0 and prefs.log_render:
            seq = lum.render_collection[len(lum.render_collection)-1]
            file_path = seq.file_path
            seq_name = seq.name
            if any(ext in file_path for ext in glob_vars.keys()):
                file_path = replace_globals(file_path)
            if any(ext in seq_name for ext in glob_vars.keys()):
                seq_name = replace_globals(seq_name)

            seq_dir = os.path.realpath(bpy.path.abspath(os.path.split(file_path)[0]))
            seq_ext = seq.image_format if not preview_filetype else preview_filetype
            sequence_name = "{}.{}".format(file_path, seq_ext)

            self.file_sequence(
                filepath = os.path.join(seq_dir,"{}.{}".format(seq_name, seq.image_format)), 
                digits = seq.padded_zeros, 
                extension = preview_filetype)
            
        else:
            frame_path = bpy.path.abspath(scn.render.frame_path(frame=scn.frame_start, preview=False))
            default_flag = True
            """ Try default operator in the first place """ 
            if self.is_sequence(frame_path):
                bpy.ops.render.play_rendered_anim()
            else:
                self.file_sequence(filepath = frame_path, extension = preview_filetype)
                if self._image_sequence:
                    start = next(iter(self._image_sequence.keys()))
                    frame_path = next(iter(self._image_sequence.values()))
                    self.report({'WARNING'},"Sequence has offset and starts at {}".format(start))
                seq_dir, output_filename = os.path.split(frame_path)
                num_suffix = self.number_suffix(output_filename) #os.path.splitext(output_filename)[0]
                sequence_name = output_filename.replace(num_suffix,'#'*len(num_suffix))
        
        if not self._image_sequence:
            self.report({'WARNING'},"No sequence in loom cache")
            return {'CANCELLED'}
        else:
            if preview_filetype: 
                self.report({'WARNING'},"Preview Playback")
            if not default_flag:
                self.report({'INFO'},"Sequence from loom cache")
            else:
                self.report({'INFO'}, "Matching sequence ({}) found in {}".format(sequence_name, seq_dir))

        frames = sorted(list(self._image_sequence.keys()))
        start_frame = frames[0] 
        end_frame = frames[-1]

        """ Use preview range if enabled """
        if scn.use_preview_range:
            preview_start = scn.frame_preview_start
            preview_end = scn.frame_preview_end
            if all(x in frames for x in (preview_start, preview_end)):
                start_frame, end_frame = preview_start, preview_end
                frames = frames[frames.index(start_frame):frames.index(end_frame)]

        start_frame_path = self._image_sequence[frames[0]] # next(iter(self._image_sequence.values()))
        start_frame_suff = self.number_suffix(start_frame_path)
        start_frame_format = start_frame_path.replace(start_frame_suff,'#'*len(start_frame_suff))

        """ Detect missing frames """
        missing_frame_list = self.missing_frames(frames)
        if missing_frame_list:
            end_frame = missing_frame_list[0]-1
            self.report({'WARNING'}, "Missing Frames: {}".format(', '.join(map(str, missing_frame_list))))
            
        if not prefs.user_player:
            """ Assemble arguments and run the command """
            self.report({'INFO'}, "[Loom-OP Playback] {} {}-{}".format(sequence_name, start_frame, end_frame))
            self.report({'INFO'}, "Playblast Frame {}-{}".format(start_frame, end_frame))
            args = ["-a", "-f", str(scn.render.fps), str(scn.render.fps_base), "-s", str(start_frame), 
                    "-e", str(end_frame), "-j", str(scn.frame_step), start_frame_path]

            #bpy.ops.loom.run_terminal(arguments=" ".join(args), terminal_instance=False)
            bpy.ops.loom.run_terminal( 
                #debug_arguments=self.debug,
                terminal_instance=False,
                argument_collection=self.pack_arguments(args),
                force_bash=False)

        else: 
            """ Changes some scenes properties temporarily... Bullshit!
            However, the only way using the default operator at the moment """
            outfile = scn.render.filepath
            file_format = scn.render.image_settings.file_format
            scn.render.filepath = start_frame_format
            timeline = (scn.frame_start, scn.frame_end)
            
            scn.frame_start = start_frame
            scn.frame_end = end_frame
            if preview_filetype: scn.render.image_settings.file_format = 'JPEG'

            self.report({'INFO'}, "[Default-OP Playback] {}".format(sequence_name))
            self.report({'INFO'}, "Playblast {}-{}".format(start_frame, end_frame))

            bpy.ops.render.play_rendered_anim() # Try it again!

            scn.frame_start = timeline[0]
            scn.frame_end = timeline[1]
            scn.render.filepath = outfile
            scn.render.image_settings.file_format = file_format

        return {'FINISHED'}


# -------------------------------------------------------------------
#    Utilities
# -------------------------------------------------------------------

class LOOM_OT_clear_dialog(bpy.types.Operator):
    """Clear Log Collection"""
    bl_idname = "loom.clear_log"
    bl_label = "Clear Log"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        context.scene.loom.render_collection.clear()
        return {'FINISHED'}


class LOOM_OT_verify_terminal(bpy.types.Operator):
    """Search and verify system terminal"""
    bl_idname = "loom.verify_terminal"
    bl_label = "Verify Terminal"
    bl_options = {'INTERNAL'}

    def verify_app(self, cmd):
        try:
            subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
        return True

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences

        if platform.startswith('win32'):
            prefs.terminal = 'win-default'

        elif platform.startswith('darwin'):
            prefs.terminal = 'osx-default'
            prefs.bash_flag = True

        elif platform.startswith('linux'):

            if self.verify_app(["x-terminal-emulator", "--help"]):
                prefs.terminal = 'x-terminal-emulator'
            elif self.verify_app(["xfce4-terminal", "--help"]):
                prefs.terminal = 'xfce4-terminal'
            elif self.verify_app(["xterm", "--help"]):
                prefs.terminal = 'xterm'
            else:
                self.report({'INFO'}, "Terminal not supported.")

        elif platform.startswith('freebsd'):
            if self.verify_app(["xterm", "--help"]):
                prefs.terminal = 'xterm'

        else:
            if self.verify_app(["xterm", "--help"]):
                prefs.terminal = 'xterm'
            else:
                self.report({'INFO'}, "Terminal not supported.")

        self.report({'INFO'}, "Terminal is '{}'".format(prefs.terminal))
        #bpy.ops.wm.save_userpref()
        return {'FINISHED'}


class LOOM_PG_generic_arguments(bpy.types.PropertyGroup):
    # name: bpy.props.StringProperty()
    value: bpy.props.StringProperty()
    idc: bpy.props.IntProperty()

class LOOM_OT_run_terminal(bpy.types.Operator):
    """Run instance of an application in a new terminal"""
    bl_idname = "loom.run_terminal"
    bl_label = "Run Application in Terminal"
    bl_options = {'INTERNAL'}

    binary: bpy.props.StringProperty(
        name="Binary Path",
        description="Binary Path",
        maxlen=1024,
        subtype='FILE_PATH',
        default=bpy.app.binary_path)

    arguments: bpy.props.StringProperty(
        name="Command Line Arguments",
        description='[args …] "[file]" [args …]')

    argument_collection: bpy.props.CollectionProperty(
        name="Command Line Arguments",
        description="Allows passing a dictionary",
        type=LOOM_PG_generic_arguments)

    debug_arguments: bpy.props.BoolProperty(
        name="Debug Arguments",
        description="Print full argument list",
        default=False)

    terminal_instance: bpy.props.BoolProperty(
        name="New Terminal Instance",
        description="Opens Blender in a new Terminal Window",
        default=True)

    force_bash: bpy.props.BoolProperty(
        name="Force Bash File",
        description="Use bash file instead of passing the arguments",
        default=False)

    bash_name: bpy.props.StringProperty(
        name="Name of bash file",
        description="Name of bash file")

    communicate: bpy.props.BoolProperty(
        name="Batch process",
        description="Wait for other process",
        default=False)
    
    shutdown: bpy.props.BoolProperty(
        name="Hibernate when done",
        description="Hibernate when done",
        default=False)

    pause: bpy.props.BoolProperty(
        name="Confirm when done",
        description="Confirm when done",
        default=True)

    def single_bash_cmd(self, arg_list):
        #l = [i for s in arg_list for i in s]
        return ["{b}{e}{b}".format(b='\"', e=x) \
            if x.startswith("import") else x for x in arg_list]

    def write_bat(self, bat_path, bat_args):
        try:
            fp = open(bat_path, "w")
            fp.write("@ECHO OFF\n") #fp.write('COLOR 7F\n')
            if isinstance(bat_args[0], list):
                bat_args = [[self.binary] + i if self.binary else i for i in bat_args]
                # Double quotes and double percentage %%
                bat_args = [["{b}{e}{b}".format(b='\"', e=x) \
                    if x.startswith("import") or '\\' in x else x for x in args] \
                    for args in bat_args] #  or os.path.isfile(x)
                bat_args = [[x.replace("%", "%%") for x in args] for args in bat_args]
                for i in bat_args:
                    fp.write(" ".join(i) + "\n")
            else:
                bat_args = [self.binary] + bat_args if self.binary else bat_args
                bat_args = ["{b}{e}{b}".format(b='\"', e=x) \
                    if '\\' in x or x.startswith("import") else x for x in bat_args] # or os.path.isfile(x)
                bat_args = [x.replace("%", "%%") for x in bat_args]
                fp.write(" ".join(bat_args) + "\n")

            if self.shutdown:
                fp.write('shutdown -s\n')
            if self.pause:
                fp.write('pause\n')
            fp.write('echo Loom Rendering and Encoding done.\n')
            fp.close()
        except:
            self.report({'INFO'}, "Something went wrong while writing the bat file")
            return {'CANCELLED'}

    def write_bash(self, bash_path, bash_args):
        try:
            fp = open(bash_path, 'w')
            fp.write('#! /bin/sh\n')
            bl_bin = '"{}"'.format(self.binary) # if platform.startswith('darwin') else self.binary
            
            if isinstance(bash_args[0], list):
                bash_args = [[bl_bin] + i if self.binary else i for i in bash_args]

                """ Add quotes to python command """
                bash_args = [["{b}{e}{b}".format(b='\"', e=x) \
                    if x.startswith("import") else x for x in args] for args in bash_args]
                """ Add quotes to blend file path """
                bash_args = [["{b}{e}{b}".format(b='\"', e=x) \
                    if x.endswith(".blend") else x for x in args] for args in bash_args]
                """ Write the the file """
                for i in bash_args:
                    fp.write(" ".join(i) + "\n")
            else:
                bash_args = [bl_bin] + bash_args if self.binary else bash_args
                """ Add quotes to python command """
                bash_args = ["{b}{e}{b}".format(b='\"', e=x) \
                    if x.startswith("import") else x for x in bash_args]
                """ Add quotes to blend file path """
                bash_args = ["{b}{e}{b}".format(b='\"', e=x) \
                    if x.endswith(".blend") else x for x in bash_args]
                """ Write the the file """
                fp.write(" ".join(bash_args) + "\n")
            
            if self.pause: # https://stackoverflow.com/a/17778773
                fp.write('read -n1 -r -p "Press any key to continue..." key\n')
            if self.shutdown:
                fp.write('shutdown\n')
            fp.write('exit')
            fp.close()
            os.chmod(bash_path, 0o777)
        except:
            self.report({'WARNING'}, "Something went wrong while writing the bash file")
            return {'CANCELLED'}

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        args_user = []

        if not prefs.is_property_set("terminal") or not prefs.terminal:
            bpy.ops.loom.verify_terminal()

        if not prefs.terminal:
            self.report({'INFO'}, "Terminal not supported")
            return {'CANCELLED'}

        if self.arguments:
            '''
            Limitation: Splits the string by any whitspace, single or double quotes
            Could be improved with a regex to find the 'actual paths'
            '''
            pattern = r"""('[^']+'|"[^"]+"|[^\s']+)"""
            args_filter = re.compile(pattern, re.VERBOSE)
            lines = self.arguments.splitlines()
            for c, line in enumerate(lines):
                args_user.append(args_filter.findall(" ".join(lines)))
        
        elif len(self.argument_collection) > 0:
            #idcs = set([item.idc for item in self.argument_collection]) 
            arg_dict = {}
            for item in self.argument_collection:
                arg_dict.setdefault(item.idc, []).append(item.value)
            for key, args in arg_dict.items():
                args_user.append(args)

        if not args_user:
            self.report({'INFO'}, "No Arguments")
            return {'CANCELLED'}

        if self.bash_name:
            addon_folder = bpy.utils.script_path_user() # tempfile module?
            ext = ".bat" if prefs.terminal == 'win-default' else ".sh"
            prefs.bash_file = os.path.join(addon_folder, "{}{}".format(self.bash_name, ext))
        
        """ Allow command stacking """
        if len(args_user) > 1 and not self.communicate:
            self.force_bash = True

        """ Compile arguments for each terminal """
        if prefs.terminal == 'win-default':
            # ['start', 'cmd /k', self.binary, '-h', '&& TIMEOUT 1']
            args = [self.binary] + args_user[0] if self.binary else args_user[0]
            if self.force_bash:
                args = prefs.bash_file 

        elif prefs.xterm_flag:
            """ Xterm Fallback """ # https://bugs.python.org/issue12247
            xterm = ['xterm'] if not platform.startswith('darwin') else ['/usr/X11/bin/xterm']
            args = xterm + ["-e", self.binary] if self.binary else xterm + ["-e"]
            if self.force_bash:
                args = xterm + ["-e", prefs.bash_file]
            else:
                args += args_user[0] # Single command

        elif prefs.terminal == 'osx-default':
            """ OSX """
            #args = ["open", "-n", "-a", "Terminal", "--args", prefs.bash_file]
            #args = ["osascript", "-e", 'Tell application "Terminal" to do script "{} ;exit"'.format(quote(prefs.bash_file))]
            from shlex import quote
            activate = ["-e", 'Tell application "Terminal" to activate'] if not prefs.render_background else []
            run_bash = ["-e", 'Tell application "Terminal" to do script "{} ;exit"'.format(quote(prefs.bash_file))]
            args = ["osascript"] + activate + run_bash
            self.force_bash = True
            
        elif prefs.terminal in ['x-terminal-emulator', 'xterm']:
            """ Debian & FreeBSD """
            args = [prefs.terminal, "-e", self.binary] if self.binary else [prefs.terminal, "-e"]
            if self.force_bash:
                args = [prefs.terminal, "-e", prefs.bash_file]
            else:
                args += args_user[0] # Single command

        elif prefs.terminal in ['xfce4-terminal']: 
            """ Arch """
            args = [prefs.terminal, "-e"]
            if self.force_bash:
                args += [prefs.bash_file]
            else:
                args_xfce = self.single_bash_cmd(args_user[0])
                args_xfce = [self.binary] + args_xfce if self.binary else args_xfce
                args.append(" ".join(str(i) for i in args_xfce)) # Needs to be a string!               

        """ Print all compiled arguments """
        if self.debug_arguments:
            
            debug_list = args_user if not isinstance(args_user[0], list) \
                else [" ".join(i) for i in args_user] #else [i for sl in args_user for i in sl]
            '''
            if not any(os.path.isfile(x) and (x.endswith(".blend")) for x in debug_list):
                self.report({'INFO'}, "No blend-file provided")
            '''
            self.report({'INFO'}, "User Arguments: {}\n".format(
                ' '.join('\n{}: {}'.format(*k) for k in enumerate(debug_list))))
            if self.force_bash:
                self.report({'INFO'}, "Commands will be written to Bash: {}".format(args))
            else:
                self.report({'INFO'}, "Command: {}".format(args))
            return {'CANCELLED'}

        """ Write the file """ 
        if self.force_bash:
            if not platform.startswith('win32'):
                self.write_bash(prefs.bash_file, args_user)
            else:
                self.write_bat(prefs.bash_file, args_user)
        
        """ Open Terminal & pass all argements """
        try:
            if not self.terminal_instance:
                env_copy = os.environ.copy()
                subprocess.Popen(args, env=env_copy)
            
            elif platform.startswith('win32'):
                p = subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
                if self.communicate: p.communicate()

            else:
                # subprocess.call(args), same as Popen().wait(), print ("PID", p.pid)
                p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if self.communicate: p.communicate()

            return {'FINISHED'}
        
        except Exception as e:
            self.report({'ERROR'}, "Couldn't run command {} \nError: {}".format(
                        ' '.join('\n{}: {}'.format(*k) for k in enumerate(args)), str(e)))
            return {'CANCELLED'}


class LOOM_OT_delete_bash_files(bpy.types.Operator):
    """Delete temporary bash file"""
    bl_idname = "loom.delete_bashfiles"
    bl_label = "Delete temporary Bash File"
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences

        rem_lst = []
        for f in os.scandir(bpy.utils.script_path_user()):
            if f.name.endswith((".sh", ".bat")) and \
                f.name.startswith("loom-") and f.is_file():
                    try:
                        os.remove(f.path)
                        rem_lst.append(f.name)
                    except:
                        pass
        if rem_lst:
            self.report({'INFO'}, "{} removed.".format(", ".join(rem_lst)))
            prefs.bash_file = ""
        else:
            self.report({'INFO'}, "Nothing to remove")
        return {'FINISHED'}


class LOOM_OT_delete_file(bpy.types.Operator):
    """Deletes a file by given path"""
    bl_idname = "loom.delete_file"
    bl_label = "Remove a File"
    bl_options = {'INTERNAL'}
    
    file_path: bpy.props.StringProperty()
    message_success: bpy.props.StringProperty(default="File removed")
    message_error: bpy.props.StringProperty(default="No file")
    
    def execute(self, context):
        try:
            os.remove(self.file_path)
            self.report({'INFO'}, self.message_success)
            return {'FINISHED'}
        except:
            self.report({'WARNING'}, self.message_error)
            return {'CANCELLED'}


class LOOM_OT_utils_create_directory(bpy.types.Operator):
    """Create a directory based on a given path"""
    bl_idname = "loom.create_directory"
    bl_label = "Create given directory"
    bl_options = {'INTERNAL'}
    
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        if not self.directory:
            self.report({'WARNING'},"No directory specified")
            return {'CANCELLED'}
        
        abs_path = bpy.path.abspath(self.directory)

        '''
        head, tail = os.path.split(abs_path)
        if not os.path.isdir(head):
            self.report({'WARNING'},"Access denied: '{}' does not exists".format(head))
            return {'CANCELLED'}
        else:
        '''
        if not os.path.exists(abs_path):
            os.makedirs(abs_path)
            self.report({'INFO'},"'{}' created".format(abs_path))
        else:
            self.report({'INFO'},"'{}' already in place".format(abs_path))
        return {'FINISHED'}
    
    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


class LOOM_OT_utils_marker_unbind(bpy.types.Operator):
    """Unbind Markers in Selection"""
    bl_idname = "loom.unbind_markers"
    bl_label = "Unbind Markers from Cameras in Selection"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return any(m for m in context.scene.timeline_markers if m.select)
    
    def execute(self, context):
        marker_candidates = [m for m in context.scene.timeline_markers if m.select]
        for m in marker_candidates:
            m.camera = None
        self.report({'INFO'}, "Detached {} Marker(s)".format(len(marker_candidates))) 
        
        return {'FINISHED'}
        

class LOOM_OT_utils_marker_rename(bpy.types.Operator):
    """Rename Markers in Selection"""
    bl_idname = "loom.rename_markers"
    bl_label = "Rename Markers in Selection"
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "new_name"
    
    new_name: bpy.props.StringProperty(
        name="New Name",
        default="$SCENE_$LENS_$F4_###")
    
    @classmethod
    def poll(cls, context):
        return any(m for m in context.scene.timeline_markers if m.select)
    
    def execute(self, context):
        frame_curr = context.scene.frame_current
        markers = [m for m in context.scene.timeline_markers if m.select]
        markers = sorted(markers, key=lambda m: m.frame)
        for c, m in enumerate(markers):
            frame_flag = False
            marker_name = self.new_name
            if "$" in marker_name:
                context.scene.frame_set(m.frame)
                marker_name = replace_globals(marker_name)
                frame_flag = True
            if "#" in marker_name:
                hashes = self.new_name.count("#")
                number = "{n:0{digits}d}".format(n=c, digits=hashes)
                marker_name = marker_name.replace("#"*hashes, number)
            m.name = marker_name
        
        if frame_flag:   
            context.scene.frame_set(frame_curr)
        return {'FINISHED'}
        
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        layout = self.layout
        layout.row().prop(self, "new_name")
        layout.row()
        

class LOOM_OT_utils_marker_generate(bpy.types.Operator):
    """Add Markers from Cameras in Selection"""
    bl_idname = "loom.generate_markers"
    bl_label = "Add Markers based on Selected Cameras"
    bl_options = {'REGISTER', 'UNDO'}

    def set_playhead(self, context):
        if self.playhead:
            self.frame = context.scene.frame_current
        else:
            self.frame = max(
                context.scene.frame_start, 
                max([m.frame for m in context.scene.timeline_markers], default=1))
                
    offset: bpy.props.IntProperty(
        name="Frame Offset",
        description="Offset Markers by Frame",
        default=1, min=1)
        
    frame: bpy.props.IntProperty(
        name="Insert on Frame",
        default=1)
    
    sort_reverse: bpy.props.BoolProperty(
        name = "Add Camera Markers in reverse Order",
        default = False)

    playhead: bpy.props.BoolProperty(
        name = "Insert Markers at Playhead Position",
        default = False,
        update=set_playhead)

    @classmethod
    def poll(cls, context):
        return any(c for c in context.selected_objects if c.type == 'CAMERA')
        
    def execute(self, context):
        cam_candidates = [c for c in context.selected_objects if c.type == 'CAMERA']
        if not cam_candidates:
            self.report({'INFO'}, "No Cameras in Selection")
            return {"CANCELLED"}
        
        cam_candidates = sorted(
            cam_candidates, 
            key=lambda o: o.name, 
            reverse=self.sort_reverse)
        
        if self.playhead:
            self.frame = context.scene.frame_current
            
        markers = context.scene.timeline_markers
        marker_frames = sorted(m.frame for m in markers)
        
        for cam in cam_candidates:
            if self.frame in marker_frames:
                m = [m for m in markers if m.frame==self.frame][0]
                m.name = cam.name
            else:            
                m = markers.new(cam.name, frame=self.frame)
            m.camera = cam
            self.frame += self.offset
            
        self.report({'INFO'}, "Added {} Markers".format(len(cam_candidates)))
        return {'FINISHED'}
        
    def invoke(self, context, event):
        if self.playhead:
            self.frame = context.scene.frame_current
        return context.window_manager.invoke_props_dialog(self, width=500)
    
    def draw(self, context):
        scn = context.scene        
        layout = self.layout
        layout.separator()
        row = layout.row()
        split = row.split(factor=0.9, align=True)
        c = split.column(align=True)
        c.prop(self, "frame")
        c.enabled = not self.playhead
        col = split.column(align=True)
        col.prop(self, "playhead", icon='NLA_PUSHDOWN', text="")
        row = layout.row()
        row.prop(self, "sort_reverse", icon='SORTALPHA')
        row = layout.row()
        row.prop(self, "offset")        
        layout.separator()


class LOOM_OT_select_project_directory(bpy.types.Operator, ExportHelper):
    """Select Project Directory using the File Browser"""
    bl_idname = "loom.select_project_directory"
    bl_label = "Project Directory"
    bl_options = {'INTERNAL'}

    filename_ext = ""
    use_filter_folder = True
    cursor_pos = [0,0]
    
    def display_popup(self, context):
        win = context.window #win.cursor_warp((win.width*.5)-100, (win.height*.5)+100)
        win.cursor_warp(x=self.cursor_pos[0], y=self.cursor_pos[1]+100) # re-invoke the dialog
        bpy.ops.loom.set_project_dialog('INVOKE_DEFAULT')

    def cancel(self, context):
        self.display_popup(context)

    def invoke(self, context, event):
        self.cursor_pos = [event.mouse_x, event.mouse_y]
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    
    def execute(self, context):
        scn = context.scene
        lum = scn.loom
        lum.project_directory = os.path.dirname(self.filepath)
        self.display_popup(context)
        return {'FINISHED'}


class LOOM_OT_project_dialog(bpy.types.Operator):
    """Loom — Set Project Dialog"""
    bl_idname = "loom.set_project_dialog"
    bl_label = "Setup Project Directory"
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(name="Project Directory")
    
    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        scn = context.scene
        lum = scn.loom

        if not bpy.data.is_saved:
            self.report({'ERROR'}, "Blend-file not saved.")
            bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
            return {'CANCELLED'}

        if not self.directory or not os.path.isdir(self.directory):
            self.report({'ERROR'}, "Please specify a valid Project Directory")
            bpy.ops.loom.set_project_dialog('INVOKE_DEFAULT')
            return {'CANCELLED'}

        errors = []
        prefs = context.preferences.addons[__name__].preferences
        for d in prefs.project_directory_coll:
            if d.creation_flag and d.name:
                pdir = os.path.join(self.directory, d.name)
                bpy.ops.loom.create_directory(directory=pdir)
                if not os.path.isdir(bpy.path.abspath(pdir)):
                    errors.append(d.name)
                if any(x in d.name.lower() for x in ["rndr", "render"]):
                    if os.path.isdir(bpy.path.abspath(pdir)) and \
                        scn.render.filepath.startswith(("/tmp", "/temp")) or \
                        scn.render.filepath == "//":
                        scn.render.filepath = bpy.path.relpath(pdir) + "/"

        if not errors:
            self.report({'INFO'}, "All directories successfully created")
        else:
            self.report({'WARNING'}, 
                "Something went wrong while creating [{0}]".format(
                    ', '.join(map(str, errors))))
        
        return {'FINISHED'}

    def invoke(self, context, event):
        prefs = context.preferences.addons[__name__].preferences
        lum = context.scene.loom
        if not context.scene.loom.project_directory:
            lum.project_directory = bpy.path.abspath('//')
        self.directory = lum.project_directory
        return context.window_manager.invoke_props_dialog(self, width=prefs.project_dialog_width)

    def check(self, context):
        return True

    def draw(self, context):
        prefs = context.preferences.addons[__name__].preferences
        scn = context.scene
        lum = scn.loom
        
        layout = self.layout
        row = layout.row()
        row.template_list(
            listtype_name = "LOOM_UL_directories", 
            list_id = "", 
            dataptr = prefs,
            propname = "project_directory_coll", 
            active_dataptr = prefs,
            active_propname = "project_coll_idx", 
            rows=6)
        
        col = row.column(align=True)
        col.operator(LOOM_OT_directories_ui.bl_idname, icon='ADD', text="").action = 'ADD'
        col.operator(LOOM_OT_directories_ui.bl_idname, icon='REMOVE', text="").action = 'REMOVE'
        layout.separator()
        row = layout.row(align=True)
        row.prop(self, "directory")
        row.operator(LOOM_OT_select_project_directory.bl_idname, icon='FILE_FOLDER', text="")
        layout.separator()


class LOOM_OT_bake_globals(bpy.types.Operator):
    """Apply Globals or Restore Filepaths"""
    bl_idname = "loom.globals_bake"
    bl_label = "Bake Globals"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    
    action: bpy.props.EnumProperty(
        name="Action",
        description="Apply or Restore Paths",
        default = 'APPLY',
        items=(
            ('RESET', "Restore User Paths", "", "RECOVER_LAST", 1),
            ('APPLY', "Apply Globals", "", "WORLD_DATA", 2)))

    def out_nodes(self, scene):
        tree = scene.node_tree
        return [n for n in tree.nodes if n.type=='OUTPUT_FILE'] if tree else []

    def execute(self, context):
        scn = context.scene    
        prefs = context.preferences.addons[__name__].preferences
        glob_vars = prefs.global_variable_coll
        lum = scn.loom

        '''
        if any(ext in scn.render.filepath for ext in glob_vars.keys()):
            scn.render.filepath = replace_globals(scn.render.filepath)
        for node in self.out_nodes(scn):
            node.base_path = replace_globals(node.base_path)
            if "LAYER" in node.format.file_format:
                for slot in node.layer_slots:
                    slot.name = replace_globals(slot.name)
            else:
                for slot in node.file_slots:
                    slot.path = replace_globals(slot.path)
        # DEBUG
        for i in lum.path_collection:
            print (30*"-")
            print (i.name)
            print (i.orig, i.repl)
            for s in i.slts:
                print (s.orig, s.repl)
        '''

        if self.action == 'APPLY':
            """ Main output path """
            if any(ext in scn.render.filepath for ext in glob_vars.keys()):
                item = lum.path_collection.get("Output Path")
                if not item:
                    item = lum.path_collection.add()
                item.name = "Output Path" #item.id = 
                item.orig = scn.render.filepath
                compiled_path = replace_globals(scn.render.filepath)
                item.repl = compiled_path
                # Set the regular file path 
                scn.render.filepath = compiled_path

            """ Output nodes """
            for node in self.out_nodes(scn):
                item = lum.path_collection.get(node.name)
                if not item:
                    item = lum.path_collection.add()
                item.name = node.name
                item.orig = node.base_path
                item.repl = replace_globals(node.base_path)
                # Set the base path
                if item.repl: node.base_path = item.repl

                if "LAYER" in node.format.file_format:
                    for slot in node.layer_slots:
                        slt = item.slts.get(slot.name)
                        if not slt:
                            slt = item.slts.add()
                        slt.name = slot.name
                        slt.orig = slot.name
                        slt.repl = replace_globals(slot.name)
                        # Set the slot name
                        if slt.repl: slot.name = slt.repl
                else:
                    for slot in node.file_slots:
                        slt = item.slts.get(slot.path)
                        if not slt:
                            slt = item.slts.add()
                        slt.name = slot.path
                        slt.orig = slot.path
                        slt.repl = replace_globals(slot.path)
                        if slt.repl: slot.path = slt.repl
            self.report({'INFO'}, "Replaced all Globals")
        
        if self.action == 'RESET':
            for i in lum.path_collection:
                if i.name == "Output Path":
                    scn.render.filepath = i.orig
                else:
                    node = scn.node_tree.nodes.get(i.name)
                    if node:
                        node.base_path = i.orig
                        if "LAYER" in node.format.file_format:
                            for slot in node.layer_slots:
                                for o in i.slts:
                                    if o.repl == slot.name:
                                        slot.name = o.orig
                        else:
                            for slot in node.file_slots:
                                for o in i.slts:
                                    if o.repl == slot.path:
                                        slot.path = o.orig
            self.report({'INFO'}, "Reset all Paths")
        return {'FINISHED'}


class LOOM_OT_utils_framerange(bpy.types.Operator):
    bl_idname = "loom.shot_range"
    bl_label = "Shot Range"
    bl_description = "Set Frame Range to 1001-1241"
    bl_options = {'REGISTER', 'UNDO'}
    
    start: bpy.props.IntProperty(
        name="Start Frame",
        description="Custom start frame",
        default=1001)
    
    end: bpy.props.IntProperty(
        name="End Frame",
        description="Custom end frame",
        default=1241)

    @classmethod
    def poll(cls, context):
        return context.area.type == 'DOPESHEET_EDITOR'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def execute(self, context):
        bpy.ops.action.view_all()
        context.scene.frame_start = self.start
        context.scene.frame_end = self.end
        context.scene.frame_current = self.start
        bpy.ops.action.view_all()
        return{'FINISHED'}

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, "start")
        row.prop(self, "end")
        layout.separator(factor=0.5)


# -------------------------------------------------------------------
#    Presets
# -------------------------------------------------------------------

class LOOM_OT_render_preset(AddPresetBase, bpy.types.Operator):
    """Store or remove the current render settings as new preset"""
    bl_idname = 'loom.render_preset'
    bl_label = 'Add a new Render Preset'
    preset_menu = 'LOOM_MT_render_presets'

    preset_subdir = "loom/render_presets"
    preset_defines = [
                    'context = bpy.context',
                    'scene = context.scene',
                    'render = scene.render'
                     ]

    # References:
    # -> ./api/current/bpy.types.Menu.html#preset-menus
    # -> https://blender.stackexchange.com/a/211543
    # -> scripts/statup/preset.py

    @property
    def preset_values(self):
        context = bpy.context
        scene = context.scene
        preset_flags = scene.loom.render_preset_flags
        ignore_attribs = ('_', 'bl_', 'rna', 'reg', 'unreg', 'name')
        
        """ Defaults """
        preset_values = [
            "render.engine",
            'render.film_transparent',
            'render.fps',
            'render.fps_base',
            'render.frame_map_new',
            'render.frame_map_old',
            'render.threads',
            'render.use_high_quality_normals',
            'render.use_motion_blur',
            'render.use_persistent_data',
            #'render.use_simplify',
            'render.use_overwrite',
            'render.use_placeholder'
        ]
        
        """ User Flags """
        if preset_flags.include_resolution:
            preset_values += [
                            'render.resolution_x',
                            'render.resolution_y',
                            'render.filter_size',
                            'render.pixel_aspect_x',
                            'render.pixel_aspect_y',
                            'render.use_border',
                            'render.use_crop_to_border',
                            ]

        if preset_flags.include_output_path:
            preset_values.append('render.filepath')
        
        if preset_flags.include_scene_settings:
            preset_values += [
                            'scene.camera',
                            #'scene.background_set',
                            #'scene.active_clip'
                            ]

        if preset_flags.include_color_management:
            preset_values += [
                            'scene.display_settings.display_device',
                            'scene.view_settings.view_transform',
                            'scene.view_settings.look',
                            'scene.view_settings.exposure',
                            'scene.view_settings.gamma',
                            'scene.view_settings.use_curve_mapping',
                            ]

        if preset_flags.include_metadata:
            preset_values += [
                            'render.use_stamp',
                            'render.use_stamp_camera',
                            'render.use_stamp_date',
                            'render.use_stamp_filename',
                            'render.use_stamp_frame',
                            'render.use_stamp_frame_range',
                            'render.use_stamp_hostname',
                            'render.use_stamp_labels',
                            'render.use_stamp_lens',
                            'render.use_stamp_marker',
                            'render.use_stamp_memory',
                            'render.use_stamp_note',
                            'render.use_stamp_render_time',
                            'render.use_stamp_scene',
                            'render.use_stamp_sequencer_strip',
                            'render.use_stamp_time',
                            ]
        
        if preset_flags.include_post_processing:
            preset_values += [
                            'render.use_compositing',
                            'render.use_sequencer',
                            'render.dither_intensity',
                            ]
        
        if preset_flags.include_passes:
            for prop in dir(context.view_layer):
                if prop.startswith("use_"):
                    preset_values.append("context.view_layer.{}".format(prop))

        if preset_flags.include_file_format:
            preset_values += [
                            'render.image_settings.file_format',
                            'render.image_settings.color_mode',
                            'render.image_settings.color_depth',
                            ]
            image_settings = scene.render.image_settings
            if image_settings.file_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
                preset_values += [
                                'render.image_settings.exr_codec', 
                                'render.image_settings.use_zbuffer',
                                'render.image_settings.use_preview'
                                ]
            if image_settings.file_format in ('TIFF'):
                preset_values += ['render.image_settings.tiff_codec']
            if image_settings.file_format in ('JPEG'):
                preset_values += ['render.image_settings.quality']
        
        """ Engine Settings """
        if preset_flags.include_engine_settings:
            if scene.render.engine == 'CYCLES':
                for prop in dir(scene.cycles):
                    if not prop.startswith(ignore_attribs):
                        preset_values.append("scene.cycles.{}".format(prop))
                        
            if bpy.context.scene.render.engine == 'BLENDER_EEVEE':
                for prop in dir(scene.eevee):
                    if not prop.startswith(ignore_attribs + ("gi_cache_info",)):
                        preset_values.append("scene.eevee.{}".format(prop))
            
            if scene.render.engine == 'BLENDER_WORKBENCH':
                for prop in dir(scene.display.shading):
                    if not prop.startswith(ignore_attribs + ("selected_studio_light","cycles",)):
                        preset_values.append("scene.display.shading.{}".format(prop))
                for prop in dir(scene.display):
                    if not prop.startswith(ignore_attribs + ("shading",)):
                        preset_values.append("scene.display.{}".format(prop))

        return preset_values


class LOOM_MT_render_presets(bpy.types.Menu): 
    bl_label = 'Loom Render Presets' 
    preset_subdir = 'loom/render_presets'
    preset_operator = 'script.execute_preset'
    draw = bpy.types.Menu.draw_preset

class LOOM_PT_render_presets(PresetPanel, bpy.types.Panel):
    bl_label = 'Loom Render Presets'
    preset_subdir = 'loom/render_presets'
    preset_operator = 'script.execute_preset'
    preset_add_operator = 'loom.render_preset'
    #def draw(self, context): pass


def draw_loom_preset_flags(self, context):
    """Append preset flags to preset dialog"""
    preset_flags = context.scene.loom.render_preset_flags
    layout = self.layout
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.separator(factor=0.5)
    layout.emboss='NORMAL'
    col = layout.column(heading="Also include:")
    #act = col.column()
    #act.prop(preset_flags, "include_engine_settings")
    #act.enabled = False
    col.prop(preset_flags, "include_resolution")
    col.prop(preset_flags, "include_file_format")
    col.prop(preset_flags, "include_output_path")
    col.prop(preset_flags, "include_scene_settings", text="Scene Camera")
    col.prop(preset_flags, "include_passes")
    col.prop(preset_flags, "include_color_management")
    col.prop(preset_flags, "include_metadata")
    #col.prop(preset_flags, "include_post_processing")
    layout.separator(factor=0.3)

def draw_loom_preset_header(self, context):
    """Prepend header to preset dialog"""
    layout = self.layout #layout.label(text="Render Presets", icon='RENDER_STILL')
    preset_dir = context.preferences.addons[__name__].preferences.render_presets_path
    row = layout.row(align=True)
    row.operator(LOOM_OT_open_folder.bl_idname, icon="RENDER_STILL", text="", emboss=False).folder_path = preset_dir
    row.label(text=" Loom Render Presets")
    layout.separator(factor=0.3)


# -------------------------------------------------------------------
#    Panels and Menus
# -------------------------------------------------------------------

class LOOM_MT_render_menu(bpy.types.Menu):
    bl_label = "Loom"
    bl_idname = "LOOM_MT_render_menu"

    def draw(self, context):
        prefs = context.preferences.addons[__name__].preferences
        layout = self.layout
        layout.operator(LOOM_OT_render_dialog.bl_idname, icon='SEQUENCE') #RENDER_ANIMATION, SEQ_LUMA_WAVEFORM
        layout.operator(LOOM_OT_batch_dialog.bl_idname, icon='FILE_MOVIE', text="Batch Render and Encode") 
        layout.operator(LOOM_OT_encode_dialog.bl_idname, icon='RENDER_ANIMATION', text="Encode Image Sequence")
        if prefs.playblast_flag:
            layout.operator(LOOM_OT_playblast.bl_idname, icon='PLAY', text="Loom Playblast")
        layout.separator()
        #layout.operator(LOOM_OT_project_dialog.bl_idname, icon="OUTLINER") #PRESET
        layout.operator(LOOM_OT_open_output_folder.bl_idname, icon='FOLDER_REDIRECT')
        layout.operator(LOOM_OT_rename_dialog.bl_idname, icon="SORTALPHA")
        if bpy.app.version < (3, 0, 0): # Test again, if released
            layout.operator(LOOM_OT_open_preferences.bl_idname, icon='PREFERENCES', text="Loom Preferences")

def draw_loom_render_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.menu(LOOM_MT_render_menu.bl_idname, icon='RENDER_STILL')


class LOOM_MT_marker_menu(bpy.types.Menu):
    bl_label = "Loom"
    bl_idname = "LOOM_MT_marker_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator(LOOM_OT_utils_marker_generate.bl_idname, icon='CON_CAMERASOLVER', text="Markers from Cameras")
        layout.operator(LOOM_OT_utils_marker_unbind.bl_idname, icon='UNLINKED', text="Unbind Selected Markers")
        layout.operator(LOOM_OT_utils_marker_rename.bl_idname, icon='FONT_DATA', text="Batch Rename Markers")
        
def draw_loom_marker_menu(self, context):
    layout = self.layout
    layout.separator()
    #layout.menu(LOOM_MT_marker_menu.bl_idname)
    layout.operator(LOOM_OT_utils_marker_generate.bl_idname, icon='CON_CAMERASOLVER', text="Markers from Cameras")
    layout.operator(LOOM_OT_utils_marker_unbind.bl_idname, icon='UNLINKED', text="Unbind Selected Markers")
    layout.operator(LOOM_OT_utils_marker_rename.bl_idname, icon='FONT_DATA', text="Batch Rename Markers")


def draw_loom_version_number(self, context):
    """Append Version Number Slider to the Output Area"""
    if re.search("v\d+", context.scene.render.filepath) is not None:
        glob_vars = context.preferences.addons[__name__].preferences.global_variable_coll
        output_folder, file_name = os.path.split(bpy.path.abspath(context.scene.render.filepath))
        if any(ext in output_folder for ext in glob_vars.keys()):
            output_folder = replace_globals(output_folder)
        else:
            output_folder = os.path.dirname(context.scene.render.frame_path())

        layout = self.layout
        row = layout.row(align=True)
        row.prop(context.scene.loom, "output_render_version") #NODE_COMPOSITING
        row.prop(context.scene.loom, "output_sync_comp", text="", toggle=True, icon="IMAGE_RGB_ALPHA")

        '''
        row = row.row(align=True)
        row.enabled = os.path.isdir(output_folder)
        row.operator(LOOM_OT_open_folder.bl_idname, icon="DISK_DRIVE", text="").folder_path = output_folder
        #layout.separator()
        '''


def draw_loom_outputpath(self, context):
    """Append compiled file path using globals to the Output Area"""
    prefs = context.preferences.addons[__name__].preferences
    glob_vars = prefs.global_variable_coll
    scn = context.scene

    if prefs.output_extensions or not scn.render.filepath:
        return

    output_folder, file_name = os.path.split(bpy.path.abspath(scn.render.filepath))
    output_folder = os.path.realpath(output_folder)
    
    if not file_name and bpy.data.is_saved:
        blend_name, ext = os.path.splitext(os.path.basename(bpy.data.filepath))
        file_name = blend_name + "_" # What about a dot?

    if not file_name.count('#'): # and not scn.loom.is_rendering:
        if not bool(re.search(r'\d+\.[a-zA-Z0-9]{3,4}\b', file_name)):
            file_name = "{}{}".format(file_name, "#"*4)
    else:
        file_name = re.sub(r"(?!#+$|#+\.[a-zA-Z0-9]{3,4}\b)#+", '', file_name)
    
    globals_flag = False
    if any(ext in file_name for ext in glob_vars.keys()):
        file_name = replace_globals(file_name)
        globals_flag = True
    if any(ext in output_folder for ext in glob_vars.keys()):
        output_folder = replace_globals(output_folder)
        globals_flag = True

    if file_name.endswith(tuple(scn.render.file_extension)):
        file_path = os.path.join(output_folder, file_name)
    else:
        file_path = os.path.join(output_folder, "{}{}".format(file_name, scn.render.file_extension))

    layout = self.layout
    box = layout.box()
    row = box.row()

    if not os.path.isdir(output_folder): #if globals_flag
        row.operator(LOOM_OT_utils_create_directory.bl_idname, 
            icon='ERROR', text="", emboss=False).directory = os.path.dirname(file_path)
    else:
        row.operator(LOOM_OT_open_output_folder.bl_idname, icon='DISK_DRIVE', text="", emboss=False)

    if scn.render.is_movie_format:
        row.label(text="Video file formats are not supported by Loom")
    else:
        row.label(text="{}".format(file_path if not scn.loom.is_rendering else scn.render.filepath))

    if globals_flag or context.scene.loom.path_collection:
        sub_row = row.row(align=True)
        if len(context.scene.loom.path_collection):
            sub_row.operator(LOOM_OT_bake_globals.bl_idname, icon="RECOVER_LAST", text="").action='RESET'
        sub_row.operator(LOOM_OT_bake_globals.bl_idname, icon="WORLD_DATA", text="").action='APPLY'
        #sub_row.operator_enum(LOOM_OT_bake_globals.bl_idname, "action", icon_only=True)
    layout.separator(factor=0.1)


def draw_loom_compositor_paths(self, context):
    """Display File Output paths to the Output Area"""
    if bpy.context.preferences.addons[__name__].preferences.output_extensions:
        return
    scene = context.scene
    if all([hasattr(scene.node_tree, "nodes"), scene.render.use_compositing, scene.use_nodes]):
        output_nodes = [n for n in scene.node_tree.nodes if n.type=='OUTPUT_FILE']
        if len(output_nodes) > 0:
            lum = scene.loom
            layout = self.layout
            layout.separator()
            box = layout.box()
            row = box.row()
            row.label(text="Compositor Output Nodes", icon='NODETREE')
            icon = 'MODIFIER' if lum.comp_image_settings else 'MODIFIER_DATA'
            row.prop(lum, "comp_image_settings", icon=icon, text="", emboss=False)
                    
            for o in output_nodes:
                row = box.row()
                i = "IMAGE_PLANE" if o.format.file_format == 'OPEN_EXR_MULTILAYER' else "RENDERLAYERS"
                row.prop(o, "base_path", text="{}".format(o.name), icon=i)
                '''
                if not os.path.isdir(o.base_path):
                    row.operator(LOOM_OT_utils_create_directory.bl_idname, 
                        icon='ERROR', text="", emboss=False).directory = os.path.dirname(o.base_path)
                '''
                row.operator(LOOM_OT_open_folder.bl_idname, 
                    icon='DISK_DRIVE', text="", emboss=False).folder_path = o.base_path

                if lum.comp_image_settings:
                    col = box.column()
                    col.template_image_settings(o.format, color_management=False)
                    col.separator()

            box.separator()
            #box.row().operator(LOOM_OT_utils_node_cleanup.bl_idname)
            layout.separator()
            

def draw_loom_project(self, context):
    """Append project dialog to app settings"""
    layout = self.layout
    layout.separator()
    layout.operator(LOOM_OT_project_dialog.bl_idname, icon="OUTLINER")


class LOOM_PT_dopesheet(bpy.types.Panel):
    """Dopesheet Render Options"""
    bl_label = "Loom"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 11

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator(LOOM_OT_open_folder.bl_idname, icon="RENDER_STILL", text="", emboss=False).folder_path = "//"
        row.label(text=" Loom")
        row = layout.row()

        col = layout.column()
        #col.label(text="Loom", icon='RENDER_STILL')
        #col = layout.column()
        row = col.row(align=True)
        row.prop(context.scene.loom, "scene_selection", icon="SCENE_DATA", text="") #icon='SHAPEKEY_DATA', 
        ka_op = row.operator(LOOM_OT_selected_keys_dialog.bl_idname, text="Render Selected Keyframes")
        ka_op.limit_to_object_selection = context.scene.loom.scene_selection
        #row.prop(context.scene.loom, "scene_range", icon="CON_ACTION", text="")
        #ka_op.limit_to_scene_frames = context.scene.loom.scene_range
        
        row = col.row(align=True)
        row.prop(context.scene.loom, "all_markers_flag", icon="TEMP", text="") #"TIME"
        ma_txt = "Render All Markers" if context.scene.loom.all_markers_flag else "Render Active Markers"
        ma_op = row.operator(LOOM_OT_selected_makers_dialog.bl_idname, text=ma_txt) # icon='PMARKER_ACT',
        ma_op.all_markers = context.scene.loom.all_markers_flag #PMARKER_SEL

        col.separator()
        col.operator(LOOM_OT_render_dialog.bl_idname, icon='SEQUENCE')
        col.separator(factor=1.0)

def draw_loom_dopesheet(self, context):
    """Append popover to the dopesheet"""
    if not context.preferences.addons[__name__].preferences.timeline_extensions:
        layout = self.layout
        row = layout.row()
        if context.space_data.mode == 'TIMELINE':
            row.operator(LOOM_OT_utils_framerange.bl_idname, text="", icon='TRACKING_FORWARDS_SINGLE')
        row.separator()
        row.popover(panel=LOOM_PT_dopesheet.__name__, text="", icon='SEQUENCE')


def draw_loom_render_presets(self, context):
    """Append render presets to the header of the Properties Area"""
    layout = self.layout
    layout.emboss = 'NONE'
    row = layout.row(align=True)
    """
    row.menu(LOOM_MT_render_presets.__name__, text=LOOM_MT_render_presets.bl_label)
    row.operator(OT_AddMyPreset.bl_idname, text="", icon='ADD')
    row.operator(OT_AddMyPreset.bl_idname, text="", icon='REMOVE').remove_active = True
    row.label(text="Render Presets")
    """
    row.popover(panel=LOOM_PT_render_presets.__name__, text="", icon='PRESET')


# -------------------------------------------------------------------
#    Registration & Shortcuts
# -------------------------------------------------------------------

addon_keymaps = []
user_keymap_ids = []

global_var_defaults = {
    "$BLEND": 'bpy.path.basename(bpy.context.blend_data.filepath)[:-6]',
    "$F4": '"{:04d}".format(bpy.context.scene.frame_current)',
    "$SCENE": 'bpy.context.scene.name',
    "$CAMERA": 'bpy.context.scene.camera.name',
    "$LENS": '"{:0.0f}mm".format(bpy.context.scene.camera.data.lens)',
    "$VIEWLAYER": 'bpy.context.view_layer.name',
    "$MARKER": 'next((i.name for i in bpy.context.scene.timeline_markers if i.frame == bpy.context.scene.frame_current), "NO_NAME")',
    "$COLL": 'bpy.context.collection.name',
    "$OB": 'bpy.context.active_object.name',
    "$SUM": 'str(sum([8, 16, 32]))'
}

project_directories = {
    1: "assets",
    2: "geometry",
    3: "textures",
    4: "render",
    5: "comp"
}

classes = (
    LOOM_PG_globals,
    LOOM_UL_globals,
    LOOM_PG_project_directories,
    LOOM_UL_directories,
    LOOM_AP_preferences,
    LOOM_OT_preferences_reset,
    LOOM_OT_globals_ui,
    LOOM_OT_directories_ui,
    LOOM_PG_render,
    LOOM_PG_batch_render,
    LOOM_PG_preset_flags,
    LOOM_PG_slots,
    LOOM_PG_paths,
    LOOM_PG_scene_settings,
    LOOM_OT_render_threads,
    LOOM_OT_render_full_scale,
    LOOM_OT_guess_frames,
    LOOM_OT_verify_frames,
    LOOM_OT_render_dialog,
    LOOM_OT_render_input_dialog,
    LOOM_OT_selected_keys_dialog,
    LOOM_OT_selected_makers_dialog,
    LOOM_MT_display_settings,
    LOOM_UL_batch_list,
    LOOM_OT_batch_dialog,
    LOOM_OT_batch_snapshot,
    LOOM_OT_batch_selected_blends,
    LOOM_OT_scan_blends,
    LOOM_OT_batch_list_actions,
    LOOM_OT_batch_clear_list,
    LOOM_OT_batch_dialog_reset,
    LOOM_OT_batch_remove_doubles,
    LOOM_OT_batch_active_item,
    LOOM_OT_batch_default_range,
    LOOM_OT_batch_verify_input,
    LOOM_OT_encode_dialog,
    LOOM_OT_rename_dialog,
    LOOM_OT_load_image_sequence,
    LOOM_OT_encode_select_movie,
    LOOM_OT_encode_verify_image_sequence,
    LOOM_OT_encode_auto_paths,
    LOOM_OT_fill_sequence_gaps,
    LOOM_OT_open_folder,
    LOOM_OT_open_output_folder,
    LOOM_OT_utils_node_cleanup,
    LOOM_OT_open_preferences,
    LOOM_OT_openURL,
    LOOM_OT_render_terminal,
    LOOM_OT_render_image_sequence,
    LOOM_OT_playblast,
    LOOM_OT_clear_dialog,
    LOOM_OT_verify_terminal,
    LOOM_PG_generic_arguments,
    LOOM_OT_run_terminal,
    LOOM_OT_delete_bash_files,
    LOOM_OT_delete_file,
    LOOM_OT_utils_create_directory,
    LOOM_OT_utils_marker_unbind,
    LOOM_OT_utils_marker_rename,
    LOOM_OT_utils_marker_generate,
    LOOM_OT_select_project_directory,
    LOOM_OT_project_dialog,
    LOOM_OT_bake_globals,
    LOOM_OT_utils_framerange,
    LOOM_OT_render_preset,
    LOOM_MT_render_presets,
    LOOM_PT_render_presets,
    LOOM_MT_render_menu,
    LOOM_MT_marker_menu,
    LOOM_PT_dopesheet
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.loom = bpy.props.PointerProperty(type=LOOM_PG_scene_settings)

    """ Hotkey registration """
    playblast = bpy.context.preferences.addons[__name__].preferences.playblast_flag
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Screen", space_type='EMPTY')
        if playblast:
            kmi = km.keymap_items.new(LOOM_OT_playblast.bl_idname, 'F11', 'PRESS', ctrl=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_project_dialog.bl_idname, 'F1', 'PRESS', ctrl=True, shift=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_rename_dialog.bl_idname, 'F2', 'PRESS', ctrl=True, shift=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_open_output_folder.bl_idname, 'F3', 'PRESS', ctrl=True, shift=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_encode_dialog.bl_idname, 'F9', 'PRESS', ctrl=True, shift=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_batch_dialog.bl_idname, 'F12', 'PRESS', ctrl=True, shift=True, alt=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new(LOOM_OT_render_dialog.bl_idname, 'F12', 'PRESS', ctrl=True, shift=True)
        kmi.active = True
        addon_keymaps.append((km, kmi))

        if platform.startswith('darwin'):
            if playblast:
                kmi = km.keymap_items.new(LOOM_OT_playblast.bl_idname, 'F11', 'PRESS', oskey=True, shift=True)
                kmi.active = True
                addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_project_dialog.bl_idname, 'F1', 'PRESS', oskey=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_rename_dialog.bl_idname, 'F2', 'PRESS', oskey=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_open_output_folder.bl_idname, 'F3', 'PRESS', oskey=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_encode_dialog.bl_idname, 'F9', 'PRESS', oskey=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_batch_dialog.bl_idname, 'F12', 'PRESS', oskey=True, shift=True, alt=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))
            kmi = km.keymap_items.new(LOOM_OT_render_dialog.bl_idname, 'F12', 'PRESS', oskey=True, shift=True)
            kmi.active = True
            addon_keymaps.append((km, kmi))


    """ Globals """
    glob = bpy.context.preferences.addons[__name__].preferences.global_variable_coll
    if not glob:
        for key, value in global_var_defaults.items():
            gvi = glob.add()
            gvi.name = key
            gvi.expr = value
    
    """ Project Directories """
    dirs = bpy.context.preferences.addons[__name__].preferences.project_directory_coll
    if not dirs:
        for key, value in project_directories.items():
            di = dirs.add()
            di.name = value
            di.creation_flag = True

    """ Menus """
    bpy.types.TOPBAR_MT_render.append(draw_loom_render_menu)
    bpy.types.TIME_MT_marker.append(draw_loom_marker_menu)
    bpy.types.DOPESHEET_MT_marker.append(draw_loom_marker_menu)
    bpy.types.NLA_MT_marker.append(draw_loom_marker_menu)
    bpy.types.RENDER_PT_output.prepend(draw_loom_outputpath)
    bpy.types.RENDER_PT_output.append(draw_loom_version_number)
    bpy.types.RENDER_PT_output.append(draw_loom_compositor_paths)
    bpy.types.DOPESHEET_HT_header.append(draw_loom_dopesheet)
    bpy.types.PROPERTIES_HT_header.append(draw_loom_render_presets)
    bpy.types.LOOM_PT_render_presets.append(draw_loom_preset_flags) 
    bpy.types.LOOM_PT_render_presets.prepend(draw_loom_preset_header)
    if bpy.app.version >= (3, 0, 0):
        bpy.types.TOPBAR_MT_blender.append(draw_loom_project)
    else:
        bpy.types.TOPBAR_MT_app.append(draw_loom_project)


def unregister():
    bpy.types.DOPESHEET_HT_header.remove(draw_loom_dopesheet)
    bpy.types.RENDER_PT_output.remove(draw_loom_compositor_paths)
    bpy.types.RENDER_PT_output.remove(draw_loom_outputpath)
    bpy.types.RENDER_PT_output.remove(draw_loom_version_number)
    bpy.types.NLA_MT_marker.remove(draw_loom_marker_menu)
    bpy.types.DOPESHEET_MT_marker.remove(draw_loom_marker_menu)
    bpy.types.TIME_MT_marker.remove(draw_loom_marker_menu)
    bpy.types.TOPBAR_MT_render.remove(draw_loom_render_menu)
    bpy.types.PROPERTIES_HT_header.remove(draw_loom_render_presets)
    bpy.types.LOOM_PT_render_presets.remove(draw_loom_preset_flags)
    bpy.types.LOOM_PT_render_presets.remove(draw_loom_preset_header)
    if bpy.app.version >= (3, 0, 0):
        bpy.types.TOPBAR_MT_blender.remove(draw_loom_project)
    else:
        bpy.types.TOPBAR_MT_app.remove(draw_loom_project)
    
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)
        
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    del bpy.types.Scene.loom
    
    
if __name__ == "__main__":
    register()
