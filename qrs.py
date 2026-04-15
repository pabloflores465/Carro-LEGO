import qrcode

for n in [1, 2, 3]:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # 30% de tolerancia
        box_size=10,   # tamaño de cada cuadrito (más grande = más fácil de leer)
        border=4,      # margen blanco alrededor (mínimo recomendado: 4)
    )
    qr.add_data(f"QR{n}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(f"qr{n}.png")
    print(f"Guardado: qr{n}.png")
