"""
Access windows credentials
Credentials must be stored in the Windows Credentials Manager in the Control
Panel. This helper will search for "generic credentials" under the section
"Windows Credentials"
Example usage::
    result = get_generic_credential('foobar')
    if result:
        print("NAME:", result.username)
        print("PASSWORD:", result.password)
    else:
        print('No matching credentials found')
Based on https://gist.github.com/exhuma/a310f927d878b3e5646dc67dfa509b42
which was based on https://gist.github.com/mrh1997/717b14f5783b49ca14310419fa7f03f6


RESOURCE: https://docs.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw

Then I hacked on it for a while because I couldn't get the original to work for unicode passwords.
I need to use this for some secure passwords at work.
I don't want to install the rather heavy pywin32 package just for this.
All native python, no dependencies. (Unless you consider Windows a dependency.)
"""
import ctypes as ct
import ctypes.wintypes as wt
from enum import Enum
from typing import NamedTuple, Optional

LP_BYTE = ct.POINTER(wt.BYTE)

Credential = NamedTuple("Credential", [("username", str), ("password", str)])


class Persist(Enum):
    """CRED_PERSIST_* enumeration (wincred.h)"""

    SESSION = 0x01
    LOCAL_MACHINE = 0x02
    ENTERPRISE = 0x03


class CredType(Enum):
    """CRED_TYPE_* enumeration (wincred.h)"""

    GENERIC = 0x01
    DOMAIN_PASSWORD = 0x02
    DOMAIN_CERTIFICATE = 0x03
    DOMAIN_VISIBLE_PASSWORD = 0x04
    GENERIC_CERTIFICATE = 0x05
    DOMAIN_EXTENDED = 0x06
    MAXIMUM = 0x07
    MAXIMUM_EX = MAXIMUM + 1000


class WinCredential(ct.Structure):
    """_CREDENTIALW structure (wincred.h)"""

    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", LP_BYTE),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ct.POINTER(wt.LPWSTR)),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


def create_generic_credential(target_name: str, username: str, password: str) -> None:
    """
    Creates a generic credential in the Windows Credential Manager.

    Args:
        target_name (str): The name of the target for the credential.
        username (str): The username for the credential.
        password (str): The password for the credential.

    Raises:
        OSError: If the credential creation fails.

    Returns:
        None
    """
    advapi32 = ct.WinDLL("Advapi32.dll")
    advapi32.CredWriteW.restype = wt.BOOL
    advapi32.CredWriteW.argtypes = [ct.POINTER(WinCredential), wt.DWORD]

    credential = WinCredential()
    credential.Type = CredType.GENERIC.value
    credential.TargetName = target_name
    credential.CredentialBlobSize = len(password.encode("utf-16-le") + b"\x00")
    credential.CredentialBlob = ct.cast(
        ct.create_string_buffer(password.encode("utf-16-le") + b"\x00"),
        ct.POINTER(ct.c_ubyte),
    )
    credential.Persist = Persist.LOCAL_MACHINE.value
    credential.UserName = username
    if not advapi32.CredWriteW(ct.byref(credential), 0):
        raise ct.WinError(ct.get_last_error())


def delete_generic_credential(target_name: str) -> None:
    """Deletes a generic credential with the specified target name.

    Args:
        target_name (str): The target name of the credential to delete.

    Raises:
        OSError: If the credential deletion fails.

    """
    advapi32 = ct.WinDLL("Advapi32.dll")
    advapi32.CredDeleteW.restype = wt.BOOL
    advapi32.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]

    if not advapi32.CredDeleteW(target_name, CredType.GENERIC.value, 0):
        raise ct.WinError(ct.get_last_error())


def get_generic_credential(name: str) -> Optional[Credential]:
    """
    Retrieves a generic credential with the specified name from the Windows Credential Manager.

    Args:
        name (str): The name of the generic credential to retrieve.

    Returns:
        Optional[Credential]: The retrieved credential if found, or None if not found.
    """
    advapi32 = ct.WinDLL("Advapi32.dll")
    advapi32.CredReadW.restype = wt.BOOL
    advapi32.CredReadW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        ct.POINTER(
            ct.POINTER(WinCredential)
        ),  ## <-- why the double wrap? I'm so confused.
    ]

    cred_ptr = ct.POINTER(WinCredential)()
    if advapi32.CredReadW(name, CredType.GENERIC.value, 0, ct.byref(cred_ptr)):
        try:
            username = cred_ptr.contents.UserName
            cred_blob = cred_ptr.contents.CredentialBlob
            cred_blob_size = cred_ptr.contents.CredentialBlobSize
            cred_str = ct.string_at(cred_blob, cred_blob_size)
            password = cred_str.decode("utf-16le", errors="ignore")
            return Credential(username, password)
        finally:
            advapi32.CredFree(cred_ptr)
    return None
