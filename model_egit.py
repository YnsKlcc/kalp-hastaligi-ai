import os
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, classification_report
import joblib
import warnings

# Terminaldeki gereksiz uyarıları gizler
warnings.filterwarnings('ignore')

# 1. DOSYA YOLUNU OTOMATİK BULMA
current_directory = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(current_directory, "heart_disease_uci.csv")

print("1. Veri seti okunuyor...")
try:
    df = pd.read_csv(file_path)
except FileNotFoundError:
    print("\nHATA: Veri seti bulunamadı!")
    exit()

# Gereksiz kolonları çıkarıyoruz
if 'id' in df.columns and 'dataset' in df.columns:
    df = df.drop(columns=['id', 'dataset'])

print("2. Veri ön işleme ve temizlik yapılıyor...")
# Hedef değişken (num): 0 Sağlıklı, >0 Hasta
df['num'] = df['num'].apply(lambda x: 1 if x > 0 else 0)

X = df.drop(columns=['num'])
y = df['num']

# Sütunları Sayısal ve Kategorik (Metin) olarak ikiye ayırma
numeric_cols = X.select_dtypes(include=['int64', 'float64']).columns
categorical_cols = X.select_dtypes(include=['object', 'bool']).columns

# Eksik verileri doldurma
num_imputer = SimpleImputer(strategy='median') # Sayılar için medyan
cat_imputer = SimpleImputer(strategy='most_frequent') # Metinler için en çok tekrar eden

if len(numeric_cols) > 0:
    X[numeric_cols] = num_imputer.fit_transform(X[numeric_cols])
if len(categorical_cols) > 0:
    X[categorical_cols] = cat_imputer.fit_transform(X[categorical_cols])

# Kategorik Verilerin Sayısal Hale Getirilmesi (LabelEncoder)
label_encoders = {}
for col in categorical_cols:
    le = LabelEncoder()
    X[col] = le.fit_transform(X[col])
    label_encoders[col] = le # İleride API'de kullanmak için kaydediyoruz

# Veri Ölçeklendirme
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

print("3. Random Forest Modeli eğitiliyor...")
X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, random_state=42)

rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
rf_model.fit(X_train, y_train)
acc = rf_model.score(X_test, y_test) * 100
print(f"Model Eğitimi Tamamlandı! Test Doğruluğu: %{acc:.2f}")

# Karmaşıklık Matrisi (Confusion Matrix) ve Detaylı Performans Metrikleri
print("\n--- Model Performans Değerlendirmesi ---")
y_pred = rf_model.predict(X_test)
cm = confusion_matrix(y_test, y_pred)

print("\nKarmaşıklık Matrisi (Confusion Matrix):")
print(cm)

if cm.shape == (2, 2):
    tn, fp, fn, tp = cm.ravel()
    print("\nMatris Detayları:")
    print(f"  - Doğru Olumsuz (True Negative - TN) : {tn} (Sağlıklı denip doğru tahmin edilen)")
    print(f"  - Yanlış Pozitif (False Positive - FP): {fp} (Sağlıklı iken yanlışlıkla hasta tahmin edilen)")
    print(f"  - Yanlış Olumsuz (False Negative - FN): {fn} (Hasta iken yanlışlıkla sağlıklı tahmin edilen)")
    print(f"  - Doğru Pozitif (True Positive - TP) : {tp} (Hasta denip doğru tahmin edilen)")

print("\nDetaylı Sınıflandırma Raporu (Classification Report):")
print(classification_report(y_test, y_pred, target_names=['Sağlıklı (0)', 'Hasta (1)']))

print("\n4. Model paketlenip kaydediliyor...")
joblib.dump(rf_model, os.path.join(current_directory, "rf_model.pkl"))
joblib.dump(scaler, os.path.join(current_directory, "scaler.pkl"))
joblib.dump(num_imputer, os.path.join(current_directory, "num_imputer.pkl"))
joblib.dump(cat_imputer, os.path.join(current_directory, "cat_imputer.pkl"))
joblib.dump(label_encoders, os.path.join(current_directory, "label_encoders.pkl"))

print("İşlem Başarılı! Tüm '.pkl' dosyaları proje klasörüne eklendi.")
