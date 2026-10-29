"""Portable authenticated encryption, with legacy Windows DPAPI support."""
import ctypes
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def crypt(value, decrypt=False):
    prefix=b'zapas:fernet:v1:'
    key=os.getenv('ZAPAS_MASTER_KEY','')
    if (decrypt and value.startswith(prefix)) or (not decrypt and key):
        if not key:
            raise ValueError('Для открытия ключей нужен ZAPAS_MASTER_KEY из резервной копии.')
        from cryptography.fernet import Fernet, InvalidToken
        try:
            cipher=Fernet(key.encode('ascii'))
            return cipher.decrypt(value[len(prefix):]) if decrypt else prefix+cipher.encrypt(value)
        except (ValueError,InvalidToken,UnicodeError) as exc:
            raise ValueError('Не удалось открыть хранилище: проверьте ZAPAS_MASTER_KEY.') from exc
    if os.name!='nt':
        raise ValueError('На сервере задайте ZAPAS_MASTER_KEY. Старые ключи Windows нужно подключить заново.')
    raw=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(raw,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    api=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('Windows не удалось открыть защищённое хранилище ключей.')
    try:
        return ctypes.string_at(target.data,target.length)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))
