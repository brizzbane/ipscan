from elasticsearch_dsl import connections, DocType
connections.connections.create_connection(hosts=['http://localhost:9200'], timeout=90)


class IpAsnRangeDoc(DocType):


    class Meta:
        index = 'ips.by_asn'

    def save(self, ** kwargs):
        return super(IpAsnRangeDoc, self).save(** kwargs)


if __name__ == '__main__':
    ir = IpAsnRangeDoc.search().query('regexp', owner=r'Amazon Data .*').execute()
    for ip in ir:
        print(ip)