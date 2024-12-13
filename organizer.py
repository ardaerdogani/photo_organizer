import os
import re

def rename_files_in_folder(folder_path):
    # Klasör var mı kontrol et
    if not os.path.exists(folder_path):
        print(f"Klasör mevcut değil: {folder_path}")
        return

    for filename in os.listdir(folder_path):
        old_path = os.path.join(folder_path, filename)

        if os.path.isfile(old_path):
            # Çift tekrarları kaldır
            new_filename = re.sub(r"akdeniz-akdeniz", "akdeniz", filename)

            # Sondaki numaraları ve tireleri temizle
            new_filename = re.sub(r"-\d+(-\d+)*", "", new_filename)

            # Uzantıyı koru
            base_name, extension = os.path.splitext(new_filename)
            new_filename = f"{base_name.strip()}{extension}"

            # Benzersiz isim oluşturmak için kontrol
            counter = 1
            unique_filename = new_filename
            while os.path.exists(os.path.join(folder_path, unique_filename)):
                unique_filename = f"{base_name}-{counter}{extension}"
                counter += 1

            # Yeni dosya yolunu oluştur
            new_path = os.path.join(folder_path, unique_filename)
            os.rename(old_path, new_path)
            print(f"'{filename}' -> '{unique_filename}'")

# Kullanıcıdan klasör yolunu alın
folder_path = input("Lütfen dosyaların bulunduğu klasörün tam yolunu girin: ")

# İşleme başla
rename_files_in_folder(folder_path)
