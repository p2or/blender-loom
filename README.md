# Loom

Loom is a Blender addon designed to simplify the process of rendering *image sequences* and *specific frames*.

### Table of Contents

- [Render Image Sequence Dialog](#render-image-sequence-dialog-ctrlshiftf12)
- [Batch Render Dialog](#batch-render-dialog-ctrlshiftaltf12)
- [Encode Image Sequence Dialog](#encode-image-sequence-dialog-ctrlshiftf9)
- [Utilities](#utilities)
- [Gotchas and Limitations](#gotchas-and-limitations)
- [Addon Preferences](#addon-preferences)
- [Installation](#installation)

## Render Image Sequence Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F12</kbd>

Using this dialog allows to render quite complex **frame ranges**, **single frames**, **subframes** as well as **exclude** frames and ranges without manipulating the *Timeline*, in the background (optional).

<!-- ![Render Image Sequence Dialog](https://i.stack.imgur.com/ppHBr.jpg) -->
<img width="550" alt="Render image sequence dialog" src="https://user-images.githubusercontent.com/512368/141286862-8e094f3f-4713-4089-a05c-80a7f87ad2a0.png">


For example, you can enter `1, 2, 3, 5-10` to render only those frames. In order to exclude e.g. frame `7` from `1-10` range, just add a *caret* or *exclamation mark* followed by the number, like `^7` to render frame `1-6, 8-10` (similar when specifying multiple ranges on the command line).

### Examples

| Input (Range)          | Output (Frames)                                        | 
|:-----------------------|:-------------------------------------------------------|
| 1, 2, 3, 5-10          | 1, 2, 3, 5, 6, 7, 8, 9, 10                             |
| 1-5, 10-15             | 1, 2, 3, 5, 10, 11, 12, 13, 14, 15                     |
| 1-10 ^7                | 1, 2, 3, 4, 6, 8, 9, 10                                |
| 1-10 ^3,4              | 1, 2, 5, 6, 8, 9, 10                                   |
| 1-10 ^3-5              | 1, 2, 6, 8, 9, 10                                      |
| 1-10 ^3-5, 9           | 1, 2, 6, 8, 10                                         |
| 1-10 23-29 ^3-5, 7-9   | 1, 2, 5, 6, 10, 23, 24, 25, 26, 27, 28, 29             |
| 1-10x2                 | 1, 3, 5, 7, 9                                          |
| 1-10x2 10              | 1, 3, 5, 7, 9, 10                                      |
| 1-10x2 10 ^5           | 1, 3, 7, 9, 10                                         |
| 1-2x0.1                | 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0  |
| 1-2x0.1 ^1.4, 1.5      | 1.0, 1.1, 1.2, 1.3, 1.6, 1.7, 1.8, 1.9, 2.0            |

You can also render **every nth frame** of the scene by adding `x` followed by any number after the range, which allows to indicate *incrementation steps*. For example, `1-10x2` renders every second frame of the given range `1, 3, 5, 7, 9`. This way, you can render **subframes** by indicating a float value like `x0.1` or even `x0.01` to get slow motion.

<!--
![Loom Slow Motion](https://i.stack.imgur.com/wOCMZ.gif)  
`1-17x0.25` @25fps *(without motion blur)*
-->

<img width="700" alt="Loom Slow Motion" src="https://i.stack.imgur.com/wOCMZ.gif"><br>
`1-17x0.25` @25fps *(without motion blur)*



### Options

 - You can **verify the output** before rendering the animation by clicking the *Verify Output* operator ![I1](https://i.stack.imgur.com/JovW0.jpg), which reports all frames that are going to be rendered (in the *Info Area* as well as in the terminal). If you hold down <kbd>Ctrl</kbd> or <kbd>Cmd</kbd> while clicking the button, all images will be output to the *Info Area* as and the terminal individually `1,2,3,4,5,7 ...`.
 - When you click the stopwatch ![stopwatch_small](https://user-images.githubusercontent.com/512368/182232254-882c38c5-6b77-46dc-8485-79697cabc02f.jpg), Loom tries to find out **which frames are missing** based on the regular *Output Path* and automatically adds them to the list. If you hold down <kbd>Ctrl</kbd> or <kbd>Cmd</kbd> while clicking the button, the regular frame range of the timeline is forced to be taken.
 - You can enable *Filtering* ![I1](https://i.stack.imgur.com/lKqJq.jpg) to handle each number after the caret (`^`) separatly, this allows to **add single frames** or frame ranges **right after any excluded frame**, e.g. `1-10 23 ^3-7 4 6` renders `1, 2, 4, 6, 8, 9, 10, 23` instead of `1, 2, 8, 9, 10, 23` if the property is disabled.

## Batch Render Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>Alt</kbd><kbd>F12</kbd>

The batch dialog allows to *render multiple .blend files* and *encoding their output sequences* by using the command line in one go. You can either scan any directory or add the .blend files manually, re-order them and specify the render range or single frames for each file similar to the *Render Image Sequence Dialog*.

<!-- ![Loom Batch Dialog](https://i.stack.imgur.com/OSbdI.jpg) -->
<img width="700" alt="Loom Batch Dialog" src="https://user-images.githubusercontent.com/512368/180617979-fad236e2-0a0f-4892-acad-3d0524b20284.png">


### Notes

 - Encoding <img width="22" alt="encoding-flag-disabled" src="https://github.com/p2or/blender-loom/assets/512368/645d1594-f20b-4f82-b1ee-c13761d812b7"> is optional and 
 takes place **after the rendering** (path tracing / rasterization) is done
 - Execution of that operator generates a **batch file** in Blender's scripts directory (`.sh` or `.bat` depending on the operating system) and runs all commands via command line one by another - you can make the generated batch files your own and modify them (for easy access, click the *disk icon* right beside *Delete temporary batch file* operator in the *Addon Preferences*, to open up the scripts directory on your system)
  - In case you need **more space** within the dialog itself or e.g. you'd like to see the **path of each file**, you can change the appearance of the elements within the dialog by clicking the arrow in the upper right corner to change the display settings

    <!-- ![Loom Batch Display Menu](https://i.stack.imgur.com/MgHPk.jpg) -->
    

## Encode Image Sequence Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F9</kbd>

If [FFmpeg](https://www.ffmpeg.org/) is installed on your machine and set up properly in the *Addon Preferences*, this dialog allows to encode image sequences to [ProRes](https://en.wikipedia.org/wiki/Apple_ProRes) or [DNxHD](https://en.wikipedia.org/wiki/Avid_DNxHD)/[DNxHR](https://en.wikipedia.org/wiki/DNxHR_codec) for preview or layout purposes. You can select any image sequence, set the output colorspace (useful for encoding linear exr sequences), the frame rate and the desired codec.

<!-- ![Encode Image Sequence Dialog](https://i.stack.imgur.com/ILENa.jpg) -->

<img width="700" alt="Encode image sequence" src="https://user-images.githubusercontent.com/512368/180618261-979aac90-a906-448c-9e9c-09028db4b50f.png">

### Options
 - Select `ProRes 4444 XQ` to get an **alpha channel**
 - You can **verify the image sequence** on disk before encoding by clicking the *Verify Image Sequence* operator ![I1](https://i.stack.imgur.com/JovW0.jpg), which also detects all frames missing frames of the sequence
 - In case there are **missing frames**, the addon either provides an utility function to fill the gaps of the image sequence with copies of the previous (nearest) frame to get the full lenght animation and another operator to render all missing frames. If you just want to fill the gaps of the given sequence, enable the *Ignore Scene Range* property <img width="18" alt="Screenshot 2022-12-15 at 08 32 33" src="https://user-images.githubusercontent.com/512368/207799961-29fb9796-941b-48dd-8ea0-e5c22a94e1e9.png">

   <!-- ![Encode Image Sequence Dialog](https://i.stack.imgur.com/ul9ld.jpg) -->


## Utilities

Loom also includes some handy utilities that help in production e.g. display of the final *Output Path*, a list of all *File Output* nodes in the regular *Output Panel*, the creation of project folders and more.

### Render Version

Once you add `v` and some arbitrary number to the *Output Path*, a new 'slider' appears in the *Output Area* which allows to change the actual version string on the fly.

<!--
![Render Version](https://user-images.githubusercontent.com/512368/180618398-cb566e95-53cd-4f37-ad1d-6aeed1b184d6.gif)
-->

<img width="700" alt="Render Version" src="https://user-images.githubusercontent.com/512368/180618398-cb566e95-53cd-4f37-ad1d-6aeed1b184d6.gif">


If you'd like to remove all version strings in your *File Output* nodes, hit <kbd>F3</kbd> and type `Remove version str...` 

### Render specific Keyframes

You can **render specific keyframes** by selecting the keys in the *Timeline*, *Dope Sheet* or the *Graph Editor* and use the popover in the header of each area to call *Render Selected Keyframes* operator which automatically adds the frames to the render list.
 
<!--![Render Selected Keyframes](https://i.stack.imgur.com/PUs71.jpg) -->
<!-- <img width="608" alt="popover" src="https://user-images.githubusercontent.com/512368/141284257-1f380f00-feb4-40eb-9f64-64df67903edb.png"> -->
<img width="700" alt="Loom timeline popover" src="https://user-images.githubusercontent.com/512368/141284381-80695c90-a6cb-4dcd-9f12-d068ec16b8f0.png">

#### Options

 - If _Limit by Object Selection_ toggle <img width="16" alt="limit_by_object-in-selection" src="https://user-images.githubusercontent.com/512368/204095616-19d16715-91be-4210-81b1-d30eb263d3bb.png">
is enabled, only the keyframes of the selected objects in the scene are added to the list.
 - Holding down <kbd>Ctrl</kbd> while clicking 'Render Selected Keyframes' button will add all keyframes to the list, regardless of what's selected in the _Timeline, Graph Editor or Dopesheet_.
 - Holding down <kbd>Alt</kbd> or <kbd>Option</kbd> while clicking the 'Render Selected Keyframes' operator limits the selection of keyframes to the current _frame range of the scene_.
 - If you hold down <kbd>Ctrl</kbd> and <kbd>Alt</kbd> or <kbd>Option</kbd> when clicking 'Render selected Keyframes' operator without any object selected, _all keyframes_ within the _frame range of the scene_ are be added to the list.

### File Path Variables

Loom allows to replace of all occurrences of any 'global variable' defined in the *Addon Preferences* via *custom python expressions* for the regular *Output Path* as well as all *File Output* nodes.

<!--
![globals](https://user-images.githubusercontent.com/512368/180618964-b4d6ef0c-c542-4925-be40-3e6b65cb9172.gif)
-->

<img width="700" alt="Globals" src="https://user-images.githubusercontent.com/512368/180618964-b4d6ef0c-c542-4925-be40-3e6b65cb9172.gif">

#### Notes 

- The replacement only works if Loom is used for rendering, e.g. using <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F12</kbd>.
- Defaults for demo purposes are `$BLEND`, `$F4`, `$SCENE`, `$CAMERA`, `$LENS`, `$MARKER`, `$COLL` etc.
- To customize the provided time variables for e.g. dailies, see the official documentation on [`strftime()`](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) for all options.



<!-- <img width="736" alt="Globals" src="https://user-images.githubusercontent.com/512368/180612932-93a1882c-06f3-4016-a2f7-13fa61c78dce.png"> -->

### Render Presets

Loom allows to store custom presets for all necessary render properties. You can set the engine, samples, output path, image properties, etc. and save all settings as a new custom 'preset' in the *Header* of the *Properties Area* for reuse.

<!--<img width="700" alt="Screenshot 2022-12-11 at 18 36 42" src="https://user-images.githubusercontent.com/512368/206920653-6de7400f-f9b0-4382-bce5-085415534c2d.png">-->

<img width="700" alt="Screenshot 2023-01-14 at 14 04 22" src="https://user-images.githubusercontent.com/512368/212472944-9fcdb914-2d21-480d-bc98-9d3100cb11f0.png">


If a preset exists, a new dropdown will appear in the 'Image Sequence Dialog' when 'Render using Command Line' is turned on as well as in the 'Batch Dialog' if 'Override Render Settings' is enabled which also allows to render the current scene or file using your custom presets, regardless of the actual render settings:

<!-- <img width="521" alt="Screenshot 2022-12-11 at 18 38 19" src="https://user-images.githubusercontent.com/512368/206921276-28bb5bc0-64ba-4bcd-8e65-4b9963e52bf2.png"> -->

<img width="700" alt="rndr_presets_dropdown" src="https://user-images.githubusercontent.com/512368/212472717-63c19b49-4e6d-4898-ba16-33777a397105.png">



### Markers

Loom adds three new operators to the *Marker* menu of the *Timeline (Timeline > Markers)*. You can **generate markers based on the selected cameras** in the viewport, **unbind the markers** from the cameras or just **batch rename** them using a custom name or *Globals* that are defined in the *Addon Preferences*.

<img width="700" alt="Batch rename markers" src="https://user-images.githubusercontent.com/512368/180614748-98ba2f5f-cb67-4c88-9714-5b63f1d09684.png">

### Rename File Sequence <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F2</kbd>

Using this dialog allows to rename any arbitrary image or file sequence on disk.

<img width="700" alt="Rename File Sequence" src="https://user-images.githubusercontent.com/512368/180613687-fc71ca7b-a415-45b1-bc27-db4332d76603.png">

### Project Setup <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F1</kbd>

Using this dialog allows to create all relevant main folders for the current project and automatically sets the *Output Path* to the render folder.

<img width="700" alt="Project Setup" src="https://user-images.githubusercontent.com/512368/180613021-0046c4b8-4ef6-4a10-bcef-f4a87660421e.png">

<!-- 
### Shot Range

Using this dialog allows set any frame range quickly and the settings can be reused for multiple projects.

<img width="350" alt="Project Setup" src="https://user-images.githubusercontent.com/512368/182233951-b196ce0c-2d03-4ca6-92fa-707993aaee89.jpg">
-->

## Addon Preferences

The available settings are slightly different per operating system. However, you can set the *size of each dialog*, set the *path to the ffmpeg binary*, access as well as remove the *batch files* and *edit the shortcuts* on all operating systems.


| Property                          | Description                                            | 
|:----------------------------------|:-------------------------------------------------------|
| *Timeline Extensions*             | Allows to either turn on or off the display of the 'Loom Popover' and 'Shot Range' dialog in the *Timeline* |
| *Output Panel Extensions*         | Allows to either turn on or off the display of all *File Output* nodes and the final *Output Path* *(Properties > Output Properties > Output)*  |
| *Playblast (Experimental)*        | Allows to playback the latest rendered image sequence by using <kbd>Shift</kbd><kbd>Ctrl</kbd><kbd>F11</kbd> hotkey (requires restarting Blender after saving the *User Preferences*) |
| *Default Animation Player*        | Force using the default *Animation Player (User Preferences > Files > Animation Player)* for *Playblast* operator |
| *Path to FFmpeg Binary*           | Only required if not already part of your linux distribution or not added to the environment variables | 
| *Force generating `.sh` or `.bat` File* | Generates a batch file for all command line operations, even if those are single ones | 
| *Delete temporary `.sh` or `.bat` File* | Removes all generated batch files found in *Blender's scripts directory* | 
| *Xterm (Terminal Fallback)*       | Fallback for all command line operations if the system terminal is not supported, [Xterm](https://en.wikipedia.org/wiki/Xterm) is available for most *Linux Distributions* and *older Versions of MacOS*| 
| *Reset Preferences*       | Reset all properties to their default values (except the binary path to ffmpeg) | 


## Gotchas and Limitations

 - Loom does not support direct encoding, make sure the *File Format* is set to *Image*.
 - Activation of the *Playblast* hotkey requires restarting Blender.
 - In case encoding fails for some reason, make sure the path to ffmpeg binary is [absolute](https://en.wikipedia.org/wiki/Path_(computing)).
 - Renewal of hotkeys *once entirely removed*, requires resetting the *Addon-Preferences* and restarting Blender.
 - Switching the terminal back from *Xterm* to the default system terminal requires resetting the *Addon-Preferences* in some cases.


## Installation

 1. Download the [latest release](https://github.com/p2or/blender-loom/releases/)
 1. In Blender open up *User Preferences > Addons*
 1. Click *Install from File*, select `loom.py` and activate the addon
 1. Save the *Preferences* and restart Blender

----

Contributions to *Loom* are welcome. Successfully tested on *Arch Linux 2017+*, *Ubuntu 16.04+*, *MacOSX 10.6.8+*, *Windows 7+*. 
