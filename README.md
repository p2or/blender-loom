# Loom

Loom is a blender addon designed to simplify the process of rendering *image sequences* and *specific frames*.

## Render Image Sequence Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F12</kbd>

Using this dialog allows to render quite complex **frame ranges**, **single frames**, **subframes** as well as **exclude** frames and ranges without manipulating the *Timeline*, in the background (optional).

<!-- ![Render Image Sequence Dialog](https://i.stack.imgur.com/ppHBr.jpg) -->
<img width="475" alt="loom" src="https://user-images.githubusercontent.com/512368/141286862-8e094f3f-4713-4089-a05c-80a7f87ad2a0.png">


For example, you can enter `1, 2, 3, 5-10` to render only those frames. In order to exclude e.g. frame `7` from `1-10` range, just add a *caret* or *exclamation mark* followed by the number, like `^7` to render frame `1-6, 8-10` (similar when specifying multiple ranges on the command line).

### Examples

| Input (Range)          | Output (Frames)                                        | 
|:-----------------------|:-------------------------------------------------------|
| 1, 2, 3, 5-10          | 1, 2, 3, 5, 6, 7, 8, 9, 10                             |
| 1-5, 10-15             | 1, 2, 3, 5, 10, 11, 12, 13, 14, 15                     |
| 1-10 ^7                | 1, 2, 3, 4, 6, 8, 9, 10                                |
| 1-10 ^3,4              | 1, 2, 5, 6, 8, 9, 10                                   |
| 1-10 ^3-5              | 1, 2, 6, 8, 9, 10                                   |
| 1-10 ^3-5, 9           | 1, 2, 6, 8, 10                                         |
| 1-10 23-29 ^3-5, 7-9   | 1, 2, 5, 6, 10, 23, 24, 25, 26, 27, 28, 29             |
| 1-10x2                 | 1, 3, 5, 7, 9                                          |
| 1-10x2 10              | 1, 3, 5, 7, 9, 10                                      |
| 1-10x2 10 ^5           | 1, 3, 7, 9, 10                                         |
| 1-2x0.1                | 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0  |
| 1-2x0.1 ^1.4, 1.5      | 1.0, 1.1, 1.2, 1.3, 1.6, 1.7, 1.8, 1.9, 2.0            |

You can also render **every nth frame** of the scene by adding `x` followed by any number after the range, which allows to indicate *incrementation steps*. For example, `1-10x2` renders every second frame of the given range `1, 3, 5, 7, 9`. This way, you can render **subframes** by indicating a float value like `x0.1` or even `x0.01` to get slow motion.

![SlowMo](https://i.stack.imgur.com/wOCMZ.gif)  
`1-17x0.25` @25fps *(without motion blur)*

### Options

 - You can **verify the output** before rendering the animation by clicking the *Verify Output* operator ![I1](https://i.stack.imgur.com/JovW0.jpg), which reports all frames that are going to be rendered (in the *Info Area* as well as in the terminal)
 - You can enable *Filtering* ![I1](https://i.stack.imgur.com/lKqJq.jpg) to handle each number after the caret (`^`) separatly, this allows to **add single frames** or frame ranges **right after any excluded frame**, e.g. `1-10 23 ^3-7 4 6` renders `1, 2, 4, 6, 8, 9, 10, 23` instead of `1, 2, 8, 9, 10, 23` if the property is disabled
 - You can **render specific keyframes** by selecting the keys in the *Timeline*, *Dope Sheet* or the *Graph Editor* and use the popover in the header of each area to call *Render Selected Keyframes* operator which automatically adds the frames to the render list:
 
    <!--![Render Selected Keyframes](https://i.stack.imgur.com/PUs71.jpg) -->
    <!-- <img width="608" alt="popover" src="https://user-images.githubusercontent.com/512368/141284257-1f380f00-feb4-40eb-9f64-64df67903edb.png"> -->
    <img width="1440" alt="popover" src="https://user-images.githubusercontent.com/512368/141284381-80695c90-a6cb-4dcd-9f12-d068ec16b8f0.png">
    
    *Tip:* If [Developer Extras](https://docs.blender.org/manual/en/latest/editors/preferences/interface.html) is enabled in the preferences, you can also press <kbd>F3</kbd> and type *Render selected keyframes...* 


## Encode Image Sequence Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>F9</kbd>

If [FFmpeg](https://www.ffmpeg.org/) is installed on your machine and set up properly in the *Addon Preferences*, this dialog allows to encode image sequences to [ProRes](https://en.wikipedia.org/wiki/Apple_ProRes) or [DNxHD](https://en.wikipedia.org/wiki/Avid_DNxHD)/[DNxHR](https://en.wikipedia.org/wiki/DNxHR_codec) for preview or layout purposes. You can select any image sequence, set the output colorspace (useful for encoding linear exr sequences), the frame rate and the desired codec.

![Encode Image Sequence Dialog](https://i.stack.imgur.com/ILENa.jpg)

### Options
 - Select `ProRes 4444 XQ` to get an **alpha channel**
 - You can **verify the image sequence** on disk before encoding by clicking the *Verify Image Sequence* operator ![I1](https://i.stack.imgur.com/JovW0.jpg), which also detects all frames missing frames of the sequence
 - In case there are **missing frames**, the addon either provides an utility function to fill the gaps of the image sequence with copies of the nearest frame to get the full lenght animation and another operator to render all missing frames:
 
   ![Encode Image Sequence Dialog](https://i.stack.imgur.com/ul9ld.jpg)
 

## Loom Batch Dialog <kbd>Ctrl</kbd><kbd>Shift</kbd><kbd>Alt</kbd><kbd>F12</kbd>

The batch dialog allows to *render multiple .blend files* and *encoding their output sequences* by using the command line in one go. You can either scan any directory or add the .blend files manually, re-order them and specify the render range or single frames for each file similar to the *Render Image Sequence Dialog*.

![Loom Batch Dialog](https://i.stack.imgur.com/OSbdI.jpg)

### Notes

 - Encoding takes place **after the rendering** (path tracing) is done
 - Execution of that operator generates a **batch file** in blender's scripts directory (`.sh` or `.bat` depending on the operating system) and runs all commands via command line one by another - you can make the generated batch files your own and modify them (for easy access, click the *disk icon* right beside *Delete temporary batch file* operator in the *Addon Preferences*, to open up blender's scripts directory on your system)
  - In case you need **more space** within the dialog itself or e.g. you'd like to see the **path of each file**, you can change the appearance of the elements within the dialog by clicking the arrow to change the display settings directly:

    ![Loom Batch Display Menu](https://i.stack.imgur.com/MgHPk.jpg)


## Addon Preferences

The available settings are slightly different per operating system. However, you can set the *size of each dialog*, set the *path to the ffmpeg binary*, access as well as remove the *batch files* and *edit the shortcuts* on all operating systems.


| Property                          | Description                                            | 
|:----------------------------------|:-------------------------------------------------------|
| *Display Buttons in Render Panel* | Displays all *loom operators* in the *Render Panel* (except the *Batch Dialog*) |                    
| *Playblast (Experimental)*        | Allows to playback the latest rendered image sequence by using <kbd>Shift</kbd><kbd>Ctrl</kbd><kbd>F11</kbd> hotkey (requires restarting blender after saving the *User Preferences*) |
| *Default Animation Player*        | Force using the default *Animation Player (User Preferences > Files > Animation Player)* for *Playblast* operator |
| *Path to FFmpeg Binary*           | Only required if not already part of your linux distribution or not added to the environment variables | 
| *Force generating `.sh` or `.bat` File* | Generates a batch file for all command line operations, even if those are single ones | 
| *Delete temporary `.sh` or `.bat` File* | Removes all generated batch files found in *blender's scripts directory* | 
| *Xterm (Terminal Fallback)*       | Fallback for all command line operations if the system terminal is not supported, [Xterm](https://en.wikipedia.org/wiki/Xterm) is available for most *Linux Distributions* and *older Versions of MacOS*| 
| *Reset Preferences*       | Reset all properties to their default values (except the binary path to ffmpeg) | 

## Gotchas and Limitations

 - Loom does not support direct encoding, make sure the *File Format* is set to *Image*
 - Activation of the *Playblast* hotkey requires restarting blender
 - In case encoding fails for some reason, make sure the path to ffmpeg binary is [absolute](https://en.wikipedia.org/wiki/Path_(computing))
 - Renewal of hotkeys *once entirely removed*, requires resetting the *Addon-Preferences* and restarting blender 
 - Switching the terminal back from *Xterm* to the default system terminal requires resetting the *Addon-Preferences* in some cases
 
## Installation

 1. Download the [latest release](https://github.com/p2or/blender-loom/releases/)
 2. In Blender open up *User Preferences > Addons*
 3. Click *Install from File*, select `loom.py` and activate the Add-on

----

Contributions to *Loom* are welcome. Successfully tested on *Arch Linux 2017+*, *Ubuntu 16.04+*, *MacOSX 10.6.8+*, *Windows 7+*. 
