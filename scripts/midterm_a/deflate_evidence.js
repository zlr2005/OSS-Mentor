// Bounded UTF-8 / raw DEFLATE transport for public evidence; no filesystem or network.
function encodeEvidence(text) {
  const utf8 = unescape(encodeURIComponent(text));
  const input = Uint8Array.from(utf8, c => c.charCodeAt(0));
  const out = []; let bits = 0, count = 0;
  function put(n, width) {
    bits |= n << count; count += width;
    while (count >= 8) { out.push(bits & 255); bits >>>= 8; count -= 8; }
  }
  function reverse(n, width) { let r=0; while(width--) { r=(r<<1)|(n&1); n>>>=1; } return r; }
  function symbol(n) {
    if(n<=143) put(reverse(48+n,8),8);
    else if(n<=255) put(reverse(400+n-144,9),9);
    else if(n<=279) put(reverse(n-256,7),7);
    else put(reverse(192+n-280,8),8);
  }
  const lengthBase=[3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
  const lengthBits=[0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
  const distanceBase=[1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
  const distanceBits=[0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
  const heads=new Int32Array(65536).fill(-1), previous=new Int32Array(input.length).fill(-1);
  function hash(i) { return ((input[i]*251+input[i+1])*251+input[i+2])&65535; }
  function insert(i) { if(i+2<input.length) { const h=hash(i); previous[i]=heads[h]; heads[h]=i; } }
  put(1,1); put(1,2); // final fixed-Huffman block
  for(let i=0;i<input.length;) {
    let length=0,distance=0;
    if(i+2<input.length) {
      let at=heads[hash(i)], attempts=80;
      const max=Math.min(258,input.length-i);
      while(at>=0 && i-at<=32768 && attempts--) {
        if(input[at]===input[i] && input[at+length]===input[i+length]) {
          let n=0; while(n<max && input[at+n]===input[i+n]) n++;
          if(n>length) { length=n; distance=i-at; if(n===max)break; }
        }
        at=previous[at];
      }
    }
    if(length>=3) {
      let l=0; while(l+1<lengthBase.length && lengthBase[l+1]<=length)l++;
      symbol(257+l);put(length-lengthBase[l],lengthBits[l]);
      let d=0;while(d+1<distanceBase.length && distanceBase[d+1]<=distance)d++;
      put(reverse(d,5),5);put(distance-distanceBase[d],distanceBits[d]);
      for(let n=0;n<length;n++)insert(i+n);i+=length;
    } else { symbol(input[i]);insert(i);i++; }
  }
  symbol(256); if(count)out.push(bits&255);
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';let b64='';
  for(let i=0;i<out.length;i+=3) {
    const n=(out[i]<<16)|((out[i+1]||0)<<8)|(out[i+2]||0);
    b64+=alphabet[(n>>>18)&63]+alphabet[(n>>>12)&63]+(i+1<out.length?alphabet[(n>>>6)&63]:'=')+(i+2<out.length?alphabet[n&63]:'=');
  }
  return {base64:b64,bytes:input.length,compressed_bytes:out.length};
}
