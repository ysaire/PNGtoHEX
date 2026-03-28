import os
import re

def convert_png_to_header(folder_path, output_file):
    with open(output_file, 'w') as hpp:
        hpp.write("#ifndef ICONS_BIN_HPP\n#define ICONS_BIN_HPP\n\n")
        
        for filename in os.listdir(folder_path):
            if filename.endswith(".png"):
                file_path = os.path.join(folder_path, filename)
                
                # nettoyage du nom de fichier pour var C++
                var_name = re.sub(r'[^a-zA-Z0-9_]', '_', filename[:-4])
                
                with open(file_path, 'rb') as f:
                    content = f.read()
                
                hpp.write(f"// File: {filename} - {len(content)} bytes\n")
                hpp.write(f"const unsigned char {var_name}[] = {{\n    ")
                
                # conversion en hex
                hex_data = [f"0x{b:02x}" for b in content]
                
                # on regroupe par 12 pour que ce soit lisible lol
                for i in range(0, len(hex_data), 12):
                    line = ", ".join(hex_data[i:i+12])
                    hpp.write(line + (",\n    " if i + 12 < len(hex_data) else "\n"))
                
                hpp.write("};\n")
                hpp.write(f"const unsigned int {var_name}_len = {len(content)};\n\n")
        
        hpp.write("#endif // ICONS_BIN_HPP\n")

# dossier courant et nom du fichier de sortie
convert_png_to_header('.', 'icons_bin.hpp')
print("fichier icons généré")