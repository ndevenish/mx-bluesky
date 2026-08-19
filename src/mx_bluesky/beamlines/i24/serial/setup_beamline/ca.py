from subprocess import PIPE, Popen


def cagetstring(pv):
    val = None
    while val is None:
        try:
            a = Popen(["caget", "-S", pv], stdout=PIPE, stderr=PIPE)
            a_stdout, a_stderr = a.communicate()
            val = a_stdout.split()[1]
            val = str(val.decode("ascii"))
        except Exception:
            print("Exception in ca_py3.py cagetstring maybe this PV aint a string")
            pass
    return val


def caget(pv):
    val = None
    while val is None:
        try:
            a = Popen(["caget", pv], stdout=PIPE, stderr=PIPE)
            a_stdout, a_stderr = a.communicate()
            val = a_stdout.split()[1].decode("ascii")
        except Exception:
            print("Exception in ca_py3.py caget, maybe this PV doesnt exist:", pv)
            pass
    return val


def caput(pv, new_val):
    check = Popen(["cainfo", pv], stdout=PIPE, stderr=PIPE)
    # print('check', check)
    check_stdout, check_stderr = check.communicate()
    if check_stdout.split()[11].decode("ascii") == "DBF_CHAR":
        a = Popen(["caput", "-S", pv, str(new_val)], stdout=PIPE, stderr=PIPE)
        a_stdout, a_stderr = a.communicate()
    else:
        a = Popen(["caput", pv, str(new_val)], stdout=PIPE, stderr=PIPE)
        a_stdout, a_stderr = a.communicate()


def caget_once(pv, timeout_s=5):
    """Read a PV once, giving up rather than retrying.

    caget above blocks until it gets a value, which is what the plans want when the
    read has to succeed for the collection to go ahead. This is for reads that are
    only diagnostic, where blocking a finished collection on an unreachable PV would
    be worse than not having the number. Returns None if the read did not work.
    """
    try:
        a = Popen(["caget", pv], stdout=PIPE, stderr=PIPE)
        a_stdout, a_stderr = a.communicate(timeout=timeout_s)
        return a_stdout.split()[1].decode("ascii")
    except Exception:
        print("Exception in ca_py3.py caget_once, maybe this PV doesnt exist:", pv)
        return None
