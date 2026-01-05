# ComfyUI Siping Custom Nodes

A collection of utility custom nodes for ComfyUI focused on file path operations and image handling.

## Features

This custom node package provides two utility nodes:

### 1. Parse File Path (拆分路径)
Parses a file path string into its components:
- **Directory**: The directory path
- **Filename (no extension)**: The filename without its extension
- **Filename (full)**: The complete filename including extension
- **Extension**: The file extension without the dot

**Inputs:**
- `file_path` (STRING): Complete file path to parse

**Outputs:**
- `directory` (STRING): Directory path
- `filename_no_ext` (STRING): Filename without extension
- `filename_full` (STRING): Complete filename with extension
- `extension` (STRING): File extension

### 2. Get Image Path & Name
Generates a file path for saving images with automatic naming:
- **Full Path**: Complete path including filename
- **Directory**: Target directory
- **Filename**: Generated filename

**Inputs:**
- `image` (IMAGE): Input image tensor
- `save_dir` (STRING): Directory to save the image (default: `./output`)
- `prefix` (STRING): Filename prefix (default: `ComfyUI_`)

**Outputs:**
- `full_path` (STRING): Complete path including directory and filename
- `directory` (STRING): Target directory path
- `filename` (STRING): Generated filename with timestamp

## Installation

1. Navigate to your ComfyUI `custom_nodes` directory:
   ```bash
   cd path/to/ComfyUI/custom_nodes
   ```

2. Clone this repository:
   ```bash
   git clone https://github.com/your-username/comfyui-siping.git
   ```

3. Restart ComfyUI

## Usage

### Parse File Path Node
Use this node when you need to extract components from a file path string. This is useful for:
- Organizing files by directory
- Renaming files while preserving extensions
- Batch file processing workflows

### Get Image Path & Name Node
Use this node to automatically generate unique file paths for saving images. Features:
- Automatic timestamp-based naming to prevent conflicts
- Customizable file prefixes
- Automatic directory creation if it doesn't exist

## Example Workflow

A typical workflow might involve:

1. Generate an image using ComfyUI nodes
2. Use "Get Image Path & Name" to generate a save path
3. Save the image using a SaveImage node
4. Use "Parse File Path" to extract components if needed for further processing

## Requirements

- ComfyUI
- Python 3.x
- PIL (Pillow)
- os, json, time (standard library modules)

## Category

All nodes in this package are categorized under: `utils/path`

## Contributing

Contributions are welcome! Feel free to submit issues or pull requests.

## License

This project is licensed under the MIT License.

## Author

Created by Harry Wood
